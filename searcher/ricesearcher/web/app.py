"""FastAPI review app (D7, FR-8).

Endpoints:
- ``GET /``                       the Slate review UI.
- ``GET /api/slices``             scored candidate slices (JSON), newest-scored first.
- ``POST /api/slices/{id}/status`` set status (the select/reject gate).
- ``PATCH /api/slices/{id}/window`` tighten the intended in/out (within the pad).
- ``/static`` and ``/cache``      UI assets and range-served local media.

The app only reads/annotates the local library and serves local files — no
posting, publishing, or upload path exists anywhere in it.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ricesearcher.config import Config, load_config
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class _StatusIn(BaseModel):
    status: str


class _WindowIn(BaseModel):
    target_in: float
    target_out: float


def _slice_dto(
    s: CandidateSlice,
    titles: dict[str, str],
    media_urls: dict[str, str],
    dup_labels: dict[str, str],
) -> dict:
    return {
        "id": s.id,
        "source_id": s.source_id,
        "source_title": titles.get(s.source_id) or f"src {s.source_id[:8]}",
        "media_url": media_urls.get(s.source_id),
        "target_in": s.target_in,
        "target_out": s.target_out,
        "pad_in": s.pad_in,
        "pad_out": s.pad_out,
        "score": s.score,
        "rationale": s.rationale,
        "heuristic_score": s.heuristic_score,
        "transcript_span": s.transcript_span,
        "rights_risk": s.rights_risk,
        "status": s.status.value,
        "dup_of": s.dup_of,
        "dup_score": s.dup_score,
        "dup_kind": s.dup_kind,
        "dup_label": dup_labels.get(s.dup_of) if s.dup_of else None,
        "scorer_model": s.scorer_model,
        "beat_profile_version": s.beat_profile_version,
    }


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    cfg.ensure_dirs()
    app = FastAPI(title="RiceSearcher Review")

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    # Range-served local media (StaticFiles handles Range requests for <video>).
    app.mount("/cache", StaticFiles(directory=str(cfg.cache_dir)), name="cache")

    def _media_url(media_path: str) -> str | None:
        try:
            rel = Path(media_path).resolve().relative_to(cfg.cache_dir.resolve())
        except ValueError:
            return None
        return "/cache/" + str(rel)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (_STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/slices")
    def list_slices(status: str | None = None) -> list[dict]:
        st = None
        if status:
            try:
                st = SliceStatus(status)
            except ValueError as exc:
                raise HTTPException(422, f"invalid status {status!r}") from exc
        with Library(cfg.db_path) as lib:
            slices = lib.list_slices(status=st)
            titles = lib.source_titles()
            media_urls = {
                src.id: _media_url(src.media_path) for src in lib.list_sources()
            }
            # Human label for whatever a slice points at as its canonical.
            dup_labels = {
                s.id: f"{titles.get(s.source_id) or s.source_id[:8]} "
                f"@ {s.target_in:.0f}-{s.target_out:.0f}s"
                for s in lib.list_slices()
            }
        return [_slice_dto(s, titles, media_urls, dup_labels) for s in slices]

    @app.post("/api/slices/{slice_id}/status")
    def set_status(slice_id: str, body: _StatusIn) -> dict:
        try:
            new = SliceStatus(body.status)
        except ValueError as exc:
            raise HTTPException(422, f"invalid status {body.status!r}") from exc
        with Library(cfg.db_path) as lib:
            s = lib.get_slice(slice_id)
            if s is None:
                raise HTTPException(404, "no such slice")
            s.status = new
            lib.upsert_slices([s])
        return {"id": slice_id, "status": new.value}

    @app.patch("/api/slices/{slice_id}/window")
    def set_window(slice_id: str, body: _WindowIn) -> dict:
        with Library(cfg.db_path) as lib:
            s = lib.get_slice(slice_id)
            if s is None:
                raise HTTPException(404, "no such slice")
            # The intended cut is tightenable but must stay inside the padded
            # window and keep in < out (ADR Q4b).
            lo, hi = sorted((body.target_in, body.target_out))
            ti = max(s.pad_in, lo)
            to = min(s.pad_out, hi)
            if to <= ti:
                raise HTTPException(422, "target_out must exceed target_in")
            s.target_in, s.target_out = ti, to
            lib.upsert_slices([s])
        return {"id": slice_id, "target_in": ti, "target_out": to}

    return app
