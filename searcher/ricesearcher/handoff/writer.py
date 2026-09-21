"""Write a handoff batch (D8, FR-9, SPEC §7).

Mirrors the RiceClipper→RicePoster mechanism: a fresh ``batch_<ts>_<rand>``
directory of ``clip_<position>.mp4`` files plus a ``manifest.json`` written LAST
via atomic rename, so a reader never sees a half-written batch. Applies the
Phase-1 integration-ledger lessons: stable batch id, strict batch-id/filename
format + resolved path containment, and no orphan artifact on failure.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from ricesearcher.acquire.watchfolder import ffprobe_duration
from ricesearcher.config import Config, load_config
from ricesearcher.handoff.extract import ClipExtractor, FfmpegClipExtractor
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source

SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class HandoffError(RuntimeError):
    """A batch could not be written to the handoff root."""


@dataclass
class HandoffEntry:
    position: int
    source_media: Path
    source_ref: str
    source_title: str
    published_at: str
    pad_in: float
    pad_out: float
    target_in: float
    target_out: float
    transcript: str
    score: float
    rationale: str
    rights_risk: str
    beat_profile_version: str
    profile_id: str


def _now() -> datetime:
    return datetime.now(UTC)


def write_batch(
    entries: list[HandoffEntry],
    *,
    extractor: ClipExtractor,
    root: Path,
) -> dict:
    """Write ``entries`` as one atomic handoff batch; return its id + clip count."""
    if not entries:
        raise HandoffError("no slices to hand off")
    positions = [e.position for e in entries]
    if len(set(positions)) != len(positions):
        raise HandoffError("clip positions must be unique")

    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    root = root.resolve()

    batch_id = f"batch_{_now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}"
    if not _SAFE_ID.match(batch_id):  # defensive: our own id is always safe
        raise HandoffError("generated an unsafe batch id")
    batch_dir = (root / batch_id).resolve()
    if batch_dir.parent != root:
        raise HandoffError("resolved batch directory escapes the handoff root")

    try:
        batch_dir.mkdir()
    except FileExistsError as exc:  # batch_id collision (negligibly rare)
        raise HandoffError(f"batch id {batch_id} already exists") from exc
    committed = False
    try:
        manifest_clips = []
        for entry in sorted(entries, key=lambda e: e.position):
            source = Path(entry.source_media)
            if not source.is_file():
                raise HandoffError(f"clip {entry.position}: source media missing")
            # Refuse a negative, degenerate, or inverted export interval rather
            # than emit an invalid range downstream (finding L1).
            if not (
                math.isfinite(entry.target_in)
                and math.isfinite(entry.target_out)
                and 0 <= entry.target_in < entry.target_out
            ):
                raise HandoffError(
                    f"clip {entry.position}: invalid window "
                    f"(need 0<=target_in<target_out)"
                )
            filename = f"clip_{entry.position}.mp4"
            dest = (batch_dir / filename).resolve()
            if dest.parent != batch_dir:
                raise HandoffError("resolved clip path escapes the batch directory")
            extractor.extract(source, entry.target_in, entry.target_out, dest)
            manifest_clips.append(_manifest_clip(entry, filename))

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "batch_id": batch_id,
            "created_at": _now().isoformat().replace("+00:00", "Z"),
            "producer": "ricesearcher",
            "clips": manifest_clips,
        }
        # Write the manifest LAST via atomic rename — the "batch complete" signal.
        tmp = batch_dir / "manifest.json.tmp"
        tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        os.replace(tmp, batch_dir / "manifest.json")
        committed = True
    finally:
        # Leave no half-written batch behind (integration-ledger lesson M-05).
        # A ``finally`` (not ``except Exception``) so cleanup also runs on
        # KeyboardInterrupt/SystemExit — e.g. Ctrl-C while ffmpeg is extracting a
        # clip — which BaseException-derived interrupts would otherwise skip,
        # orphaning a manifest-less batch dir.
        if not committed:
            # Swallow any cleanup failure so it can't mask the propagating
            # error (L2); in a ``finally`` a raised rmtree would replace it.
            try:
                shutil.rmtree(batch_dir, ignore_errors=True)
            except Exception:
                pass

    return {"batch_id": batch_id, "clip_count": len(manifest_clips)}


def _manifest_clip(entry: HandoffEntry, filename: str) -> dict:
    duration = entry.target_out - entry.target_in
    return {
        "file": filename,
        "position": entry.position,
        "source_ref": entry.source_ref,
        "source_title": entry.source_title,
        "published_at": entry.published_at,
        # Schema 1 keeps both names, but every source bound describes the exact
        # reviewed interval whose bytes are in the exported file.
        "source_window": {
            "pad_in": entry.target_in,
            "pad_out": entry.target_out,
            "target_in": entry.target_in,
            "target_out": entry.target_out,
        },
        # The reviewed cut is already applied, so the clip-relative target spans
        # the complete exported file that Clipper transcribes and renders.
        "clip": {
            "duration": duration,
            "target_in": 0.0,
            "target_out": duration,
        },
        "transcript": entry.transcript,
        "score": entry.score,
        "rationale": entry.rationale,
        "rights_risk": entry.rights_risk,
        "beat_profile_version": entry.beat_profile_version,
        "profile_id": entry.profile_id,
    }


def _entry_for(slice_: CandidateSlice, source: Source, position: int) -> HandoffEntry:
    transcript = " ".join(
        word.text
        for word in source.words
        if word.start < slice_.target_out and word.end > slice_.target_in
    )
    return HandoffEntry(
        position=position,
        source_media=Path(source.media_path),
        source_ref=source.ref,
        source_title=source.title,
        published_at=source.published_at,
        pad_in=slice_.pad_in,
        pad_out=slice_.pad_out,
        target_in=slice_.target_in,
        target_out=slice_.target_out,
        transcript=transcript,
        score=slice_.score,
        rationale=slice_.rationale,
        rights_risk=slice_.rights_risk,
        beat_profile_version=slice_.beat_profile_version,
        profile_id=slice_.profile_id,
    )


def hand_off_selected(
    library: Library,
    *,
    profile_id: str,
    extractor: ClipExtractor | None = None,
    config: Config | None = None,
    duration_prober: Callable[[Path], float] | None = None,
) -> dict:
    """Write ``selected`` slices as one handoff batch, then mark them handed_off.

    ``profile_id`` is required: only that profile's selected slices are handed
    off, so a handoff never touches another profile's rows (ADR-002 partition).
    On success the slices move to ``handed_off`` so they leave the selected queue
    and aren't re-sent; on any failure nothing is marked (retryable) and no
    partial batch is left behind. Returns ``{"batch_id", "clip_count"}``
    (``clip_count`` 0 and ``batch_id`` None if nothing is selected).
    """
    cfg = config or load_config()
    # The database write lock spans selection, manifestation, and terminal marking.
    # A second CLI/process handoff therefore re-reads after this one commits instead
    # of producing a duplicate batch from the same selected snapshot.
    with library.immediate_transaction():
        selected = library.list_slices(
            profile_id=profile_id, status=SliceStatus.SELECTED
        )
        if not selected:
            return {"batch_id": None, "clip_count": 0}

        # Position by score (best first); stable tie-break for determinism.
        ordered = sorted(selected, key=lambda s: (-s.score, s.created_at, s.id))
        entries = []
        sources: dict[str, Source] = {}
        durations: dict[str, float] = {}
        probe = duration_prober or ffprobe_duration
        for i, sl in enumerate(ordered, start=1):
            source = sources.get(sl.source_id) or library.get_source(sl.source_id)
            if source is None:
                raise HandoffError(f"slice {sl.id}: source {sl.source_id} not found")
            sources[sl.source_id] = source
            if sl.source_id not in durations:
                try:
                    duration = float(probe(Path(source.media_path)))
                except Exception as exc:
                    raise HandoffError(
                        f"clip {i}: source duration could not be verified"
                    ) from exc
                if not math.isfinite(duration) or duration <= 0:
                    raise HandoffError(
                        f"clip {i}: source duration could not be verified"
                    )
                durations[sl.source_id] = duration
            if sl.target_out > durations[sl.source_id]:
                raise HandoffError(
                    f"clip {i}: target_out {sl.target_out} exceeds source duration "
                    f"{durations[sl.source_id]}"
                )
            entries.append(_entry_for(sl, source, i))

        result = write_batch(
            entries, extractor=extractor or FfmpegClipExtractor(), root=cfg.handoff_dir
        )
        # SQLite cannot make the filesystem publish part of its transaction. A
        # process crash in this narrow interval can still require manual recovery;
        # the write lock prevents live callers from duplicating the same snapshot.
        library.bulk_update_status([sl.id for sl in ordered], SliceStatus.HANDED_OFF)
        return result
