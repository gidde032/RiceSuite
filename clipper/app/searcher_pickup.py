"""Pull a RiceSearcher handoff batch into RiceClipper's review flow.

Consumer side of the RiceSearcher→RiceClipper contract (see
``docs/integration/searcher-pickup.md``). RiceClipper reads batches RiceSearcher
wrote to its **own** handoff root ``~/ricesearcher-handoff`` (``RICECLIPPER_SEARCHER_INBOX``)
and turns each clip into a normal review job — the existing review → render →
"Send to RicePoster" flow (which writes the *separate* ``~/riceclipper-handoff``)
is unchanged. So RiceClipper is the intermediary: it *reads* the searcher inbox
and *writes* the RicePoster handoff; RiceSearcher and RicePoster never share a
directory.

This module only reads local files and copies them into job work dirs — no
posting, no network (Hard Rule #1). It mirrors RicePoster's pickup discipline:
manifest-last scan, FIFO, batch_id dedupe, validate-before-write, durable custody
(the clip bytes are copied into the job dir) before the source batch is removed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from app import jobs, probe

_INBOX_ENV = "RICECLIPPER_SEARCHER_INBOX"
_DEFAULT_INBOX = "~/ricesearcher-handoff"
_CONSUMED_FILE = ".riceclipper_consumed.json"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")


class PickupError(RuntimeError):
    """A searcher batch could not be ingested."""


def inbox_root() -> Path:
    """The RiceSearcher handoff root RiceClipper pulls from (``~`` expanded).

    Must match RiceSearcher's ``RICESEARCHER_HANDOFF_DIR``. This is NOT
    ``~/riceclipper-handoff`` (that is RiceClipper's *output* for RicePoster).
    """
    return Path(os.getenv(_INBOX_ENV) or _DEFAULT_INBOX).expanduser()


def _consumed_path(root: Path) -> Path:
    return root / _CONSUMED_FILE


def _load_consumed(root: Path) -> set[str]:
    path = _consumed_path(root)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return set(data) if isinstance(data, list) else set()
    except (json.JSONDecodeError, OSError):
        return set()


def _save_consumed(root: Path, consumed: set[str]) -> None:
    tmp = _consumed_path(root).with_suffix(".json.tmp")
    tmp.write_text(json.dumps(sorted(consumed), indent=2), encoding="utf-8")
    os.replace(tmp, _consumed_path(root))


def _read_manifest(batch_dir: Path) -> dict | None:
    """Return a parseable manifest dict, or None (manifest-less / mid-write)."""
    manifest = batch_dir / "manifest.json"
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def _oldest_unconsumed(root: Path, consumed: set[str]) -> tuple[Path, dict] | None:
    """FIFO-select the oldest batch with a manifest whose id isn't consumed."""
    candidates: list[tuple[str, Path, dict]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        data = _read_manifest(entry)
        if data is None:
            continue  # manifest-less dir = mid-write; skip
        batch_id = data.get("batch_id")
        if not isinstance(batch_id, str) or batch_id in consumed:
            continue
        candidates.append((str(data.get("created_at", "")), entry, data))
    if not candidates:
        return None
    # Oldest first by created_at, deterministic tie-break on dir name.
    candidates.sort(key=lambda c: (c[0], c[1].name))
    _, batch_dir, data = candidates[0]
    return batch_dir, data


def _validated_clips(batch_dir: Path, data: dict) -> list[dict]:
    """Validate the selected manifest fully; malformed -> PickupError, zero writes."""
    if data.get("schema_version") != 1:
        raise PickupError("unsupported manifest schema_version")
    if data.get("producer") != "ricesearcher":
        raise PickupError("manifest producer is not ricesearcher")
    batch_id = data.get("batch_id")
    if not isinstance(batch_id, str) or not _SAFE_ID.match(batch_id):
        raise PickupError("missing or unsafe batch_id")
    clips = data.get("clips")
    if not isinstance(clips, list) or not clips:
        raise PickupError("manifest has no clips")

    positions: set[int] = set()
    resolved_dir = batch_dir.resolve()
    for clip in clips:
        if not isinstance(clip, dict):
            raise PickupError("clip entry is not an object")
        pos = clip.get("position")
        if not isinstance(pos, int) or isinstance(pos, bool) or pos < 1:
            raise PickupError("clip position must be a positive integer")
        if pos in positions:
            raise PickupError("duplicate clip position")
        positions.add(pos)
        name = clip.get("file")
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise PickupError("clip file must be a bare filename")
        src = (batch_dir / name).resolve()
        if src.parent != resolved_dir or not src.is_file():
            raise PickupError(f"clip file missing or escapes the batch: {name!r}")
    return sorted(clips, key=lambda c: c["position"])


def pull_next_batch() -> dict:
    """Ingest the oldest un-consumed batch into new review jobs.

    Returns ``{"batch_id", "clip_count", "jobs": [ {job state + "title"} ]}``.
    ``batch_id`` is None / ``clip_count`` 0 when there is nothing to pull. On any
    failure, no partial ingest survives (created jobs are rolled back and the
    batch is left in place, un-consumed) so the pull is retryable.
    """
    root = inbox_root()
    if not root.is_dir():
        return {"batch_id": None, "clip_count": 0, "jobs": []}

    with jobs.job_operation_lock():
        consumed = _load_consumed(root)
        selected = _oldest_unconsumed(root, consumed)
        if selected is None:
            return {"batch_id": None, "clip_count": 0, "jobs": []}
        batch_dir, data = selected
        clips = _validated_clips(batch_dir, data)  # full validation first
        batch_id = data["batch_id"]

        created: list = []
        try:
            for clip in clips:
                src = batch_dir / clip["file"]
                job = jobs.create_job()
                created.append(job)
                dest = job.dir / f"source{Path(clip['file']).suffix or '.mp4'}"
                shutil.copy2(src, dest)  # durable custody: bytes into the job dir
                job.source_path = dest
                job.info = probe.probe(str(dest))
                job.status = "ready"
                job.searcher_title = str(clip.get("source_title", ""))
        except Exception as exc:
            _rollback(created)
            raise PickupError(f"ingest failed: {exc}") from exc

        # Custody is durable → record consumed and remove the source batch.
        consumed.add(batch_id)
        _save_consumed(root, consumed)
        shutil.rmtree(batch_dir, ignore_errors=True)

    return {
        "batch_id": batch_id,
        "clip_count": len(created),
        "jobs": [
            {**job.state().model_dump(), "title": getattr(job, "searcher_title", "")}
            for job in created
        ],
    }


def _rollback(created: list) -> None:
    """Delete jobs created during a failed ingest so a retry starts clean."""
    for job in created:
        shutil.rmtree(job.dir, ignore_errors=True)
        jobs._JOBS.pop(job.id, None)
