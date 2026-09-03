"""Write a handoff batch (D8, FR-9, SPEC §7).

Mirrors the RiceClipper→RicePoster mechanism: a fresh ``batch_<ts>_<rand>``
directory of ``clip_<position>.mp4`` files plus a ``manifest.json`` written LAST
via atomic rename, so a reader never sees a half-written batch. Applies the
Phase-1 integration-ledger lessons: stable batch id, strict batch-id/filename
format + resolved path containment, and no orphan artifact on failure.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

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
    try:
        manifest_clips = []
        for entry in sorted(entries, key=lambda e: e.position):
            source = Path(entry.source_media)
            if not source.is_file():
                raise HandoffError(f"clip {entry.position}: source media missing")
            # Refuse a degenerate/inverted window rather than emit a reversed
            # clip range downstream (finding L1).
            if not (
                entry.pad_in <= entry.target_in < entry.target_out <= entry.pad_out
            ):
                raise HandoffError(
                    f"clip {entry.position}: invalid window "
                    f"(need pad_in<=target_in<target_out<=pad_out)"
                )
            filename = f"clip_{entry.position}.mp4"
            dest = (batch_dir / filename).resolve()
            if dest.parent != batch_dir:
                raise HandoffError("resolved clip path escapes the batch directory")
            extractor.extract(source, entry.pad_in, entry.pad_out, dest)
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
    except Exception:
        # Leave no half-written batch behind (integration-ledger lesson M-05).
        # Guard cleanup so a rmtree failure can't mask the original error (L2).
        try:
            shutil.rmtree(batch_dir, ignore_errors=True)
        except Exception:
            pass
        raise

    return {"batch_id": batch_id, "clip_count": len(manifest_clips)}


def _manifest_clip(entry: HandoffEntry, filename: str) -> dict:
    duration = entry.pad_out - entry.pad_in
    return {
        "file": filename,
        "position": entry.position,
        "source_ref": entry.source_ref,
        "source_title": entry.source_title,
        "published_at": entry.published_at,
        # Provenance on the SOURCE timeline …
        "source_window": {
            "pad_in": entry.pad_in,
            "pad_out": entry.pad_out,
            "target_in": entry.target_in,
            "target_out": entry.target_out,
        },
        # … and the intended cut on the CLIP timeline (the clip IS the padded
        # window, so times are relative to its start). Clipper tightens within this.
        "clip": {
            "duration": duration,
            "target_in": max(0.0, entry.target_in - entry.pad_in),
            "target_out": min(duration, entry.target_out - entry.pad_in),
        },
        "transcript": entry.transcript,
        "score": entry.score,
        "rationale": entry.rationale,
        "rights_risk": entry.rights_risk,
        "beat_profile_version": entry.beat_profile_version,
    }


def _entry_for(slice_: CandidateSlice, source: Source, position: int) -> HandoffEntry:
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
        transcript=slice_.transcript_span,
        score=slice_.score,
        rationale=slice_.rationale,
        rights_risk=slice_.rights_risk,
        beat_profile_version=slice_.beat_profile_version,
    )


def hand_off_selected(
    library: Library,
    *,
    extractor: ClipExtractor | None = None,
    config: Config | None = None,
) -> dict:
    """Write all ``selected`` slices as one handoff batch, then mark them handed_off.

    Whole-batch by default. On success the slices move to ``handed_off`` so they
    leave the selected queue and aren't re-sent; on any failure nothing is marked
    (retryable) and no partial batch is left behind. Returns
    ``{"batch_id", "clip_count"}`` (``clip_count`` 0 and ``batch_id`` None if
    nothing is selected).
    """
    cfg = config or load_config()
    selected = library.list_slices(status=SliceStatus.SELECTED)
    if not selected:
        return {"batch_id": None, "clip_count": 0}

    sources = {s.id: s for s in library.list_sources()}
    # Position by score (best first); stable tie-break for determinism.
    ordered = sorted(selected, key=lambda s: (-s.score, s.created_at, s.id))
    entries = []
    for i, sl in enumerate(ordered, start=1):
        source = sources.get(sl.source_id)
        if source is None:
            raise HandoffError(f"slice {sl.id}: source {sl.source_id} not found")
        entries.append(_entry_for(sl, source, i))

    result = write_batch(
        entries, extractor=extractor or FfmpegClipExtractor(), root=cfg.handoff_dir
    )
    # Custody handed off only after the manifest is durably written, and in ONE
    # transaction so a crash can't leave a partial mark (finding H1). NOTE: the
    # file write and this DB mark are two phases with no shared transaction, so a
    # crash strictly between them leaves a complete batch on disk with the slices
    # still selected — a retry would re-deliver them as a new batch. The window is
    # tiny (a human-driven action) and the RiceClipper consumer should be robust
    # to the same content arriving twice (see the pickup plan / SPEC §7).
    library.bulk_update_status([sl.id for sl in ordered], SliceStatus.HANDED_OFF)
    return result
