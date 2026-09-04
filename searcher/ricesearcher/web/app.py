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

import math
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ricesearcher.config import Config, load_config
from ricesearcher.handoff.writer import HandoffError, hand_off_selected
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Serialize handoff writes: two concurrent POSTs (double-click / two tabs) would
# otherwise both read `selected` before either marks, double-delivering a batch
# (finding H2). Sync routes run in Starlette's threadpool, so a threading.Lock
# serializes them; the second then re-reads an empty selected set (a no-op).
_handoff_lock = threading.Lock()

# The review gate may only move a slice between these; `handed_off` is Phase 5's
# to set (marking a slice handed off here would bypass the actual handoff).
_GATE_STATUSES = {
    SliceStatus.CANDIDATE,
    SliceStatus.REVIEWED,
    SliceStatus.SELECTED,
    SliceStatus.REJECTED,
}


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
            # A slice's canonical may lie outside the current filter, so resolve a
            # human label only for the dup_of ids actually referenced here.
            dup_labels: dict[str, str] = {}
            for dup_id in {s.dup_of for s in slices if s.dup_of}:
                canon = lib.get_slice(dup_id)
                if canon is not None:
                    dup_labels[dup_id] = (
                        f"{titles.get(canon.source_id) or canon.source_id[:8]} "
                        f"@ {canon.target_in:.0f}-{canon.target_out:.0f}s"
                    )
        return [_slice_dto(s, titles, media_urls, dup_labels) for s in slices]

    @app.post("/api/slices/{slice_id}/status")
    def set_status(slice_id: str, body: _StatusIn) -> dict:
        try:
            new = SliceStatus(body.status)
        except ValueError as exc:
            raise HTTPException(422, f"invalid status {body.status!r}") from exc
        if new not in _GATE_STATUSES:
            raise HTTPException(
                422, f"status {new.value!r} is not settable from review"
            )
        with Library(cfg.db_path) as lib:
            current = lib.get_slice(slice_id)
            if current is None:
                raise HTTPException(404, "no such slice")
            if current.status is SliceStatus.HANDED_OFF:
                raise HTTPException(
                    409, "handed_off slices are terminal and cannot be changed"
                )
            if not lib.update_slice_status(slice_id, new):
                raise HTTPException(404, "no such slice")
        return {"id": slice_id, "status": new.value}

    @app.patch("/api/slices/{slice_id}/window")
    def set_window(slice_id: str, body: _WindowIn) -> dict:
        # Reject NaN/Infinity here (stdlib JSON parsing accepts them) with a plain
        # string detail, rather than via a pydantic constraint whose 422 body would
        # try — and fail — to serialize the NaN input.
        if not (math.isfinite(body.target_in) and math.isfinite(body.target_out)):
            raise HTTPException(422, "target_in/target_out must be finite numbers")
        if body.target_in >= body.target_out:
            raise HTTPException(422, "target_out must exceed target_in")
        with Library(cfg.db_path) as lib:
            s = lib.get_slice(slice_id)
            if s is None:
                raise HTTPException(404, "no such slice")
            # The intended cut is tightenable but stays inside the padded window
            # (ADR Q4b). pad_in/pad_out are immutable, so reading them here can't
            # be clobbered; the write itself is a targeted UPDATE (finding W1).
            ti = max(s.pad_in, body.target_in)
            to = min(s.pad_out, body.target_out)
            if to <= ti:
                raise HTTPException(422, "window is empty after clamping to the pad")
            lib.update_slice_window(slice_id, ti, to)
        return {"id": slice_id, "target_in": ti, "target_out": to}

    @app.post("/api/handoff")
    def do_handoff() -> dict:
        """Write all selected slices as a handoff batch for RiceClipper.

        Writes local files only (mirrored manifest-last batch); it never contacts
        RiceClipper or any posting surface.
        """
        with _handoff_lock, Library(cfg.db_path) as lib:
            try:
                return hand_off_selected(lib, config=cfg)
            except HandoffError as exc:
                raise HTTPException(409, str(exc)) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise HTTPException(
                    503,
                    "handoff execution failed; selected slices remain selected "
                    f"for retry: {exc}",
                ) from exc

    return app
