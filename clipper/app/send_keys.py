"""One send key, one RicePoster batch (RiceSuite functional audit W1-01).

The page gives each send to RicePoster a key and reuses it when it retries a
send whose reply it never saw. A key that already wrote a batch returns that
batch instead of writing a second one, so a lost reply can never put the same
clips in front of RicePoster twice.

Keys live in the work root, beside the jobs whose outputs they sent, and go
with them when the media cache is cleared. A process death in the instant
between writing a batch and recording its key can still let a retry write a
second batch: accepted, like the other process-death gaps in the suite SPEC.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from app import jobs

logger = logging.getLogger(__name__)

_FILE = ".handoff_send_keys.json"
_KEEP = 200  # most recent keys kept; a retry comes within minutes, not months

_LOCK = threading.Lock()
_WRITING: set[str] = set()


class SendInProgress(RuntimeError):
    """The first send with this key is still writing its batch."""


def _path() -> Path:
    return jobs._ensure_work_root() / _FILE


def _load() -> dict[str, dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def begin(key: str) -> dict | None:
    """Return the batch ``key`` already wrote, or claim ``key`` for a new write.

    Raises ``SendInProgress`` while another request with ``key`` is writing.
    A caller that gets None must call ``end(key)`` when it is done.
    """
    with _LOCK:
        done = _load().get(key)
        if isinstance(done, dict):
            return done
        if key in _WRITING:
            raise SendInProgress(key)
        _WRITING.add(key)
        return None


def record(key: str, result: dict) -> None:
    """Remember the batch ``key`` wrote. A failure is logged, not raised: the
    batch is already written and its reply must still reach the page."""
    with _LOCK:
        data = _load()
        data.pop(key, None)
        data[key] = {"batch_id": result["batch_id"], "clip_count": result["clip_count"]}
        data = dict(list(data.items())[-_KEEP:])
        try:
            path = _path()
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            logger.error("could not record send key %s: %s", key, exc)


def end(key: str) -> None:
    with _LOCK:
        _WRITING.discard(key)
