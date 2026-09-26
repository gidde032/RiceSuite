"""The home view: batches waiting at each stage (ADR-001 Q11, SPEC FR-11).

Read-only. Batches are counted from the filesystem handoff directories with
the same readiness rule the consumers use (a batch directory is complete once
its `manifest.json` exists) and from Poster's own queue API. Nothing here
writes, moves or acknowledges anything.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

# Clipper's durable registry of Searcher batch ids it has ingested
# (clipper/docs/integration/searcher-pickup.md).
CLIPPER_CONSUMED = ".riceclipper_consumed.json"


def _manifest(batch_dir: Path) -> dict | None:
    manifest = batch_dir / "manifest.json"
    if not batch_dir.is_dir() or batch_dir.is_symlink() or not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _clipper_consumed(root: Path) -> set[str]:
    try:
        data = json.loads((root / CLIPPER_CONSUMED).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    return {str(x) for x in data} if isinstance(data, list) else set()


def ready_batches(root: Path, exclude_ids: set[str] = frozenset()) -> list[dict]:
    """Complete batches in ``root``, oldest first, as small summaries."""
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.iterdir(), key=lambda p: p.name):
        if path.name.startswith("."):
            continue
        data = _manifest(path)
        if data is None:
            continue
        batch_id = str(data.get("batch_id") or path.name)
        if batch_id in exclude_ids:
            continue
        clips = data.get("clips")
        out.append(
            {
                "batch_id": batch_id,
                "created_at": data.get("created_at"),
                "clips": len(clips) if isinstance(clips, list) else 0,
            }
        )
    return out


def handoff_stages(env: Mapping[str, str]) -> dict[str, list[dict]]:
    """Batches waiting to be picked up, per handoff stage."""
    searcher_out = Path(env["RICESEARCHER_HANDOFF_DIR"])
    clipper_out = Path(env["RICECLIPPER_HANDOFF_DIR"])
    return {
        "to_clipper": ready_batches(searcher_out, _clipper_consumed(searcher_out)),
        "to_poster": ready_batches(clipper_out),
    }
