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
import logging
import os
import re
import shutil
from collections.abc import Iterable
from pathlib import Path

from ricesuite.progress import Progress, notify

from app import jobs, probe

_INBOX_ENV = "RICECLIPPER_SEARCHER_INBOX"
_DEFAULT_INBOX = "~/ricesearcher-handoff"
_CONSUMED_FILE = ".riceclipper_consumed.json"
_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
# Pulled batches not yet sent or discarded, oldest first, in the work root.
_OPEN_FILE = ".searcher_open_batches.json"
_EMPTY = {"batch_id": None, "clip_count": 0, "jobs": []}

logger = logging.getLogger(__name__)


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
    candidates = _unconsumed(root, consumed)
    if not candidates:
        return None
    _, batch_dir, data = candidates[0]
    return batch_dir, data


def _unconsumed(root: Path, consumed: set[str]) -> list[tuple[str, Path, dict]]:
    """Every complete, unconsumed batch, oldest first."""
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
    # Oldest first by created_at, deterministic tie-break on dir name.
    candidates.sort(key=lambda c: (c[0], c[1].name))
    return candidates


def waiting_batches() -> list[dict]:
    """Complete Searcher batches not yet ingested, oldest first. Read-only:
    the review UI polls this to ingest automatically (RiceSuite ADR-001 Q12)
    without taking the job lock on every poll."""
    root = inbox_root()
    if not root.is_dir():
        return []
    return [
        {
            "batch_id": data["batch_id"],
            "created_at": created_at,
            "clip_count": len(data["clips"])
            if isinstance(data.get("clips"), list)
            else 0,
        }
        for created_at, _dir, data in _unconsumed(root, _load_consumed(root))
    ]


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


def pull_next_batch(
    pull_key: str | None = None, *, progress: Progress | None = None
) -> dict:
    """Ingest the oldest un-consumed batch into durable review jobs.

    Returns ``{"batch_id", "clip_count", "jobs": [ {job state + "title"} ]}``.
    ``batch_id`` is None / ``clip_count`` 0 when there is nothing to pull. On any
    ingest failure, newly created jobs are rolled back and the batch is left in
    place, un-consumed, so the pull is retryable. If recording consumption fails
    after custody, durable job sidecars allow a later process to resume without
    creating duplicate live jobs (review lens: HIGH).

    A ``pull_key`` that already pulled a batch still open gets that batch back
    with ``replayed: true``: a page retries a pull whose reply it never saw
    with the same key, and must not get the next batch instead (W1-02).
    """
    if pull_key:
        replay = _open_payload(lambda r: r.get("pull_key") == pull_key)
        if replay["batch_id"] is not None:
            return {**replay, "replayed": True}
    root = inbox_root()
    if not root.is_dir():
        return {"batch_id": None, "clip_count": 0, "jobs": []}

    with jobs.job_operation_lock():
        # Rehydrate sidecars before selecting a batch.  This closes the normal
        # restart gap between custody and the consumed registry update.
        jobs.recover_jobs()
        consumed = _load_consumed(root)
        selected = _oldest_unconsumed(root, consumed)
        if selected is None:
            return {"batch_id": None, "clip_count": 0, "jobs": []}
        batch_dir, data = selected
        clips = _validated_clips(batch_dir, data)  # full validation first
        batch_id = data["batch_id"]
        notify(
            progress,
            "items",
            items=[
                {
                    "id": str(c["position"]),
                    "title": c.get("source_title", c["file"]),
                    "position": c["position"],
                }
                for c in clips
            ],
        )

        created: list[jobs.Job] = []
        batch_jobs: list[jobs.Job] = []
        try:
            for clip in clips:
                notify(progress, "importing", item_id=str(clip["position"]))
                src = batch_dir / clip["file"]
                job = jobs.find_searcher_job(clip, data)
                if job is not None:
                    # A sidecar may be the only record left after a restart or
                    # a failed consumed-registry write.  Never copy the same
                    # clip into a second live job.
                    if job.source_path is None or not job.source_path.is_file():
                        raise PickupError(
                            f"custody for clip {clip['position']} is unavailable"
                        )
                    batch_jobs.append(job)
                    notify(progress, "imported", item_id=str(clip["position"]))
                    continue

                job = jobs.create_job()
                created.append(job)
                dest = job.dir / f"source{Path(clip['file']).suffix or '.mp4'}"
                shutil.copy2(src, dest)  # durable custody: bytes into the job dir
                job.source_path = dest
                job.info = probe.probe(str(dest))
                job.status = "ready"
                job.searcher_title = str(clip.get("source_title", ""))
                # Keep the complete clip object and source manifest.  In
                # particular, do not trim the padded clip or import Searcher's
                # transcript timings; Clipper's transcription remains the v1
                # source of word timing.
                job.searcher_metadata = dict(clip)
                job.searcher_manifest = data
                jobs.persist_searcher_job(job)
                batch_jobs.append(job)
                notify(progress, "imported", item_id=str(clip["position"]))
        except Exception as exc:
            notify(progress, "failed", item_id=str(clip["position"]), detail=str(exc))
            _rollback(created)
            raise PickupError(f"ingest failed: {exc}") from exc

        # Custody is durable → record consumed and remove the source batch.
        notify(progress, "committing", batch_id=batch_id)
        consumed.add(batch_id)
        try:
            _save_consumed(root, consumed)
        except Exception as exc:
            # Keep the durable jobs and source batch.  A retry can match the
            # sidecars and finish this commit without duplicating live jobs.
            raise PickupError(f"could not record consumed batch: {exc}") from exc
        notify(progress, "published", batch_id=batch_id)
        shutil.rmtree(batch_dir, ignore_errors=True)
        # The source batch is gone: until it is sent or discarded, the page can
        # get it back from open_batch() if this reply never reaches it.
        records = [r for r in _load_open() if r["batch_id"] != batch_id]
        opened = {
            "batch_id": batch_id,
            "job_ids": [j.id for j in batch_jobs],
            "pull_key": pull_key,
        }
        _save_open([*records, opened])

    return _batch_payload(batch_id, batch_jobs)


def _batch_payload(batch_id: str, batch_jobs: list) -> dict:
    return {
        "batch_id": batch_id,
        "clip_count": len(batch_jobs),
        "jobs": [
            {
                **job.state().model_dump(),
                "title": job.searcher_title,
                "searcher_metadata": job.searcher_metadata,
                "searcher_manifest": job.searcher_manifest,
            }
            for job in batch_jobs
        ],
    }


# --- open batches (RiceSuite functional audit W1-02) -------------------------
# A pull removes the Searcher batch, so a lost pull reply or a page reload
# would strand its jobs in the work root with no way back to Review. The page
# asks for the oldest open batch whenever its workspace is empty, before it
# pulls anything new.


def _open_path() -> Path:
    return jobs._ensure_work_root() / _OPEN_FILE


def _load_open() -> list[dict]:
    try:
        data = json.loads(_open_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [
        r
        for r in data
        if isinstance(r, dict)
        and isinstance(r.get("batch_id"), str)
        and isinstance(r.get("job_ids"), list)
    ]


def _save_open(records: list[dict]) -> bool:
    """Write the open batches. A failure is logged, not raised: it must not
    fail a pull or a send whose own work is already done."""
    try:
        path = _open_path()
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(records, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        logger.error("could not record open Searcher batches: %s", exc)
        return False
    return True


def open_batch(batch_id: str | None = None) -> dict:
    """A pulled batch not yet sent or discarded, shaped like a pull: the one
    named ``batch_id``, or else the oldest. Consumes nothing."""
    if batch_id is None:
        return _open_payload(lambda r: True)
    return _open_payload(lambda r: r["batch_id"] == batch_id)


def open_batches() -> list[dict]:
    """Summaries of every pulled batch still in the review workspace.

    This is a read-only view for the Suite home page. Records whose jobs have
    gone away are omitted, just as ``open_batch`` omits them on restore.
    """
    with jobs.job_operation_lock():
        batches = []
        for record in _load_open():
            count = sum(
                jobs.get_job(job_id) is not None for job_id in record["job_ids"]
            )
            if count:
                batches.append({"batch_id": record["batch_id"], "clip_count": count})
        return batches


def _open_payload(wanted) -> dict:
    """The oldest open batch whose record ``wanted`` accepts. A batch none of
    whose jobs still exist (the media cache was cleared) is dropped."""
    with jobs.job_operation_lock():
        records = _load_open()
        kept: list[dict] = []
        payload = None
        for record in records:
            batch_jobs = [
                job
                for job in (jobs.get_job(i) for i in record["job_ids"])
                if job is not None
            ]
            if not batch_jobs:
                continue
            kept.append(record)
            if payload is None and wanted(record):
                payload = _batch_payload(record["batch_id"], batch_jobs)
        if kept != records:
            _save_open(kept)
        return payload or dict(_EMPTY)


def close_open_batches(job_ids: Iterable[str]) -> None:
    """A send to RicePoster closes every open batch it took clips from."""
    sent = set(job_ids)
    with jobs.job_operation_lock():
        records = _load_open()
        kept = [r for r in records if not sent.intersection(r["job_ids"])]
        if kept != records:
            _save_open(kept)


def discard_open_batch(batch_id: str) -> bool:
    """The reviewer discarded this pulled batch: stop offering it."""
    with jobs.job_operation_lock():
        records = _load_open()
        kept = [r for r in records if r["batch_id"] != batch_id]
        return kept != records and _save_open(kept)


def _rollback(created: list) -> None:
    """Delete jobs created during a failed ingest so a retry starts clean."""
    for job in created:
        shutil.rmtree(job.dir, ignore_errors=True)
        jobs._JOBS.pop(job.id, None)
