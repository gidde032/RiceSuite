"""Write a rendered batch into the RicePoster handoff directory.

Producer side of ``docs/integration/riceposter-handoff.md``. RiceClipper only
ever *writes* here — no posting, no network, no account/slot fields (SPEC §3,
Hard Rule #1). Each batch is a directory of ``clip_<position>.mp4`` files plus a
``manifest.json`` written **last** as the atomicity signal: a reader ignores any
batch directory that has no manifest, so it can never pick up a half-written
batch.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

SCHEMA_VERSION = 1
_HANDOFF_ENV = "RICECLIPPER_HANDOFF_DIR"
_DEFAULT_HANDOFF_DIR = "~/riceclipper-handoff"


class HandoffError(RuntimeError):
    """Raised when a batch cannot be written to the handoff directory."""


@dataclass
class HandoffEntry:
    position: int
    source: Path
    transcript: str = ""
    header: str = ""
    caption_style: str = "classic"
    header_style: str = "plain"


def handoff_root() -> Path:
    """Resolve the configured handoff root (env-overridable, ``~`` expanded)."""
    raw = os.getenv(_HANDOFF_ENV) or _DEFAULT_HANDOFF_DIR
    return Path(raw).expanduser()


def _now() -> datetime:
    return datetime.now(UTC)


def write_batch(entries: list[HandoffEntry], *, root: Path | None = None) -> dict:
    """Write ``entries`` as one handoff batch and return its id and clip count.

    Copies each entry's rendered mp4 to ``clip_<position>.mp4`` under a fresh
    ``batch_<ts>_<rand>`` directory, then writes ``manifest.json`` last via an
    atomic rename. Positions must be unique; every source must be a real file.
    """

    if not entries:
        raise HandoffError("no clips to hand off")

    positions = [e.position for e in entries]
    if len(set(positions)) != len(positions):
        raise HandoffError("clip positions must be unique")

    base = root or handoff_root()
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()

    batch_id = f"batch_{_now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}"
    batch_dir = (base / batch_id).resolve()
    # Directory name is server-generated, but assert containment anyway so the
    # write can never land outside the configured root.
    if batch_dir.parent != base:
        raise HandoffError("resolved batch directory escapes the handoff root")
    batch_dir.mkdir()

    manifest_clips = []
    for entry in sorted(entries, key=lambda e: e.position):
        source = Path(entry.source)
        if not source.is_file():
            raise HandoffError(f"clip {entry.position} has no rendered output")
        filename = f"clip_{entry.position}.mp4"
        dest = (batch_dir / filename).resolve()
        if dest.parent != batch_dir:
            raise HandoffError("resolved clip path escapes the batch directory")
        shutil.copy2(source, dest)
        manifest_clips.append(
            {
                "file": filename,
                "position": entry.position,
                "transcript": entry.transcript,
                "header": entry.header,
                "presets": {
                    "caption_style": entry.caption_style,
                    "header_style": entry.header_style,
                },
            }
        )

    manifest = {
        "schema_version": SCHEMA_VERSION,
        "batch_id": batch_id,
        "created_at": _now().isoformat().replace("+00:00", "Z"),
        "producer": "riceclipper",
        "clips": manifest_clips,
    }
    # Write to a temp file and atomically rename so a reader never sees a
    # partial manifest — this rename is the "batch is complete" signal.
    tmp = batch_dir / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, batch_dir / "manifest.json")

    return {"batch_id": batch_id, "clip_count": len(manifest_clips)}
