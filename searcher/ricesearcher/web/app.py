"""FastAPI review app (D7, FR-8).

Endpoints:
- ``GET /``                       the Slate review UI.
- ``GET /media``                  the media-management page.
- ``GET /api/slices``             scored candidate slices (JSON), newest-scored first.
- ``POST /api/slices/{id}/status`` set status (the select/reject gate).
- ``PATCH /api/slices/{id}/window`` set the exact source-bounded in/out.
- ``GET /api/sources``            stored sources (url, media file, slice count).
- ``POST /api/sources/{id}/delete`` full-purge one source + its media file.
- ``POST /api/cache/clear``       full-purge every source + wipe the media cache.
- ``/static`` and ``/cache``      UI assets and range-served local media.

The app only reads/annotates/deletes the local library and serves local files —
no posting, publishing, or upload path exists anywhere in it. The delete/clear
controls remove *local* data only.
"""

from __future__ import annotations

import math
import os
import subprocess
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ricesearcher.acquire.watchfolder import ffprobe_duration
from ricesearcher.beat.profile import (
    PROFILE_ID_PATTERN,
    ensure_seed,
    list_profiles,
    load_profile,
)
from ricesearcher.config import Config, load_config
from ricesearcher.handoff.writer import HandoffError, hand_off_selected
from ricesearcher.library.cache import MediaCache
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


class _HandoffIn(BaseModel):
    profile: str | None = None


def _slice_dto(
    s: CandidateSlice,
    titles: dict[str, str],
    media_urls: dict[str, str | None],
    dup_labels: dict[str, str],
    stale: bool,
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
        "profile_id": s.profile_id,
        "stale": stale,
    }


def _require_profile(profile: str | None) -> str:
    """Return a valid profile id or raise 400.

    The id rule is the loader's (ADR-002 Q1). A bad id is a client error, not
    an empty result: the UI stores the choice, so a typo must surface.
    """
    if not profile:
        raise HTTPException(400, "profile is required")
    if not PROFILE_ID_PATTERN.fullmatch(profile):
        raise HTTPException(400, f"invalid profile id {profile!r}")
    return profile


def create_app(config: Config | None = None) -> FastAPI:
    cfg = config or load_config()
    cfg.ensure_dirs()
    # Seed the legacy profile file on first use so migrated rows (all
    # ``example-beat``) always have a matching profile in the UI (ADR-002 seed).
    ensure_seed(cfg.profiles_dir)
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

    @app.get("/media", response_class=HTMLResponse)
    def media_page() -> str:
        return (_STATIC_DIR / "media.html").read_text(encoding="utf-8")

    @app.get("/profiles", response_class=HTMLResponse)
    def profiles_page() -> str:
        return (_STATIC_DIR / "profiles.html").read_text(encoding="utf-8")

    @app.get("/api/profiles")
    def list_profiles_api() -> list[dict]:
        """Every profile file, merged with per-profile row counts.

        The list is driven by the profile files; a profile with no scored rows
        gets zero counts.
        """
        with Library(cfg.db_path) as lib:
            counts = lib.profile_counts()
        out: list[dict] = []
        for p in list_profiles(cfg.profiles_dir):
            c = counts.get(p.id, {})
            out.append(
                {
                    "id": p.id,
                    "name": p.name,
                    "version": p.version,
                    "sources": c.get("sources", 0),
                    "candidates": c.get("candidates", 0),
                    "selected": c.get("selected", 0),
                    "handed_off": c.get("handed_off", 0),
                }
            )
        return out

    @app.get("/api/slices")
    def list_slices(
        profile: str | None = None, status: str | None = None
    ) -> list[dict]:
        profile = _require_profile(profile)
        st = None
        if status:
            try:
                st = SliceStatus(status)
            except ValueError as exc:
                raise HTTPException(422, f"invalid status {status!r}") from exc
        # Load the profile file once. A missing or malformed file marks every
        # slice stale (its stored version can no longer be confirmed current).
        try:
            file_version: str | None = load_profile(
                profile, profiles_dir=cfg.profiles_dir
            ).version
        except (ValueError, OSError):
            file_version = None
        with Library(cfg.db_path) as lib:
            slices = lib.list_slices(profile_id=profile, status=st)
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
        return [
            _slice_dto(
                s,
                titles,
                media_urls,
                dup_labels,
                stale=file_version is None or s.beat_profile_version != file_version,
            )
            for s in slices
        ]

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
                latest = lib.get_slice(slice_id)
                if latest is None:
                    raise HTTPException(404, "no such slice")
                raise HTTPException(
                    409, "handed_off slices are terminal and cannot be changed"
                )
        return {"id": slice_id, "status": new.value}

    @app.patch("/api/slices/{slice_id}/window")
    def set_window(slice_id: str, body: _WindowIn) -> dict:
        """Set a slice's exact export window while it is still reviewable.

        Reviewer lens: HIGH — ``handed_off`` is terminal, so a stale review
        client must not mutate the target interval after it is manifested.
        """
        # Reject NaN/Infinity here (stdlib JSON parsing accepts them) with a plain
        # string detail, rather than via a pydantic constraint whose 422 body would
        # try — and fail — to serialize the NaN input.
        if not (math.isfinite(body.target_in) and math.isfinite(body.target_out)):
            raise HTTPException(422, "target_in/target_out must be finite numbers")
        if body.target_in < 0:
            raise HTTPException(422, "target_in must be at least 0")
        if body.target_in >= body.target_out:
            raise HTTPException(422, "target_out must exceed target_in")
        with Library(cfg.db_path) as lib:
            s = lib.get_slice(slice_id)
            if s is None:
                raise HTTPException(404, "no such slice")
            if s.status is SliceStatus.HANDED_OFF:
                raise HTTPException(
                    409, "handed_off slices are terminal and cannot be changed"
                )
            source = lib.get_source(s.source_id)
            if source is None:
                raise HTTPException(409, "slice source is unavailable")

            # The review window must be bounded by the bytes that handoff will
            # receive. A stored acquisition duration can be stale, so a probe
            # failure is a hard error rather than a reason to trust that value.
            try:
                duration = float(ffprobe_duration(Path(source.media_path)))
            except Exception as exc:
                raise HTTPException(
                    422, "source duration could not be verified"
                ) from exc
            if not math.isfinite(duration) or duration <= 0:
                raise HTTPException(422, "source duration could not be verified")
            if body.target_out > duration:
                raise HTTPException(
                    422,
                    f"target_out must not exceed source duration ({duration:g}s)",
                )

            if not lib.update_slice_window(slice_id, body.target_in, body.target_out):
                latest = lib.get_slice(slice_id)
                if latest is None:
                    raise HTTPException(404, "no such slice")
                raise HTTPException(
                    409, "handed_off slices are terminal and cannot be changed"
                )
        return {
            "id": slice_id,
            "target_in": body.target_in,
            "target_out": body.target_out,
        }

    @app.post("/api/handoff")
    def do_handoff(body: _HandoffIn | None = None) -> dict:
        """Write a profile's selected slices as a handoff batch for RiceClipper.

        Writes local files only (mirrored manifest-last batch); it never contacts
        RiceClipper or any posting surface.
        """
        profile_id = _require_profile(body.profile if body else None)
        with _handoff_lock, Library(cfg.db_path) as lib:
            try:
                return hand_off_selected(lib, config=cfg, profile_id=profile_id)
            except HandoffError as exc:
                raise HTTPException(409, str(exc)) from exc
            except (OSError, subprocess.SubprocessError) as exc:
                raise HTTPException(
                    503,
                    "handoff execution failed; selected slices remain selected "
                    f"for retry: {exc}",
                ) from exc

    # -- media management (local-only delete/clear) -----------------------
    # These remove local library rows and cached bytes; they never contact any
    # external surface. "Full-purge" semantics were ratified by the maintainer
    # (2026-09-10): deleting a source also removes its scored slices.

    @app.get("/api/sources")
    def list_sources() -> list[dict]:
        with Library(cfg.db_path) as lib:
            sources = lib.list_sources()
            counts = lib.slice_counts()
        out: list[dict] = []
        for s in sources:
            try:
                size: int | None = os.path.getsize(s.media_path)
            except OSError:
                size = None  # media already gone; row still listable/deletable
            out.append(
                {
                    "id": s.id,
                    "ref": s.ref,
                    "title": s.title,
                    "kind": s.kind.value,
                    "channel": s.channel,
                    "media_url": _media_url(s.media_path),
                    "size_bytes": size,
                    "duration_s": s.duration_s,
                    "acquired_at": s.acquired_at,
                    "slice_count": counts.get(s.id, 0),
                }
            )
        return out

    @app.post("/api/sources/{source_id}/delete")
    def delete_source(source_id: str) -> dict:
        """Full-purge one source: its row, transcript, slices, and media file.

        The media file is only unlinked once no *other* source still references
        it (the cache is content-addressed, so two sources can share one file).
        """
        cache = MediaCache(cfg.cache_dir)
        with Library(cfg.db_path) as lib:
            media_path = lib.delete_source(source_id)
            if media_path is None:
                raise HTTPException(404, "no such source")
            still_shared = lib.is_media_path_referenced(Path(media_path))
        # The DB row is already gone; a failure to unlink the file (permissions,
        # read-only mount) must not 500 and imply the source survived — report
        # media_removed False and let the maintainer retry/clean up.
        media_removed = False
        if not still_shared:
            try:
                media_removed = cache.delete(Path(media_path))
            except OSError:
                media_removed = False
        return {"id": source_id, "deleted": True, "media_removed": media_removed}

    @app.post("/api/cache/clear")
    def clear_cache() -> dict:
        """Full-purge the whole library: every source, slice, and cached file."""
        cache = MediaCache(cfg.cache_dir)
        with Library(cfg.db_path) as lib:
            sources_deleted = lib.delete_all_sources()
        files_removed = cache.clear()
        return {"sources_deleted": sources_deleted, "files_removed": files_removed}

    return app
