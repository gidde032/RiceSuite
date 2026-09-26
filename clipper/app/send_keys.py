"""Each clip goes to RicePoster once (RiceSuite functional audit W1-01, S-1).

The page gives each send to RicePoster a key and reuses it when it retries a
send whose reply it never saw. A key that already wrote a batch returns that
batch instead of writing a second one. A send under any other key (a second
tab, a reload, a direct API call) that holds a clip already sent is refused
unless the reviewer confirmed a deliberate resend. So a lost reply or a second
tab can never put the same clips in front of RicePoster twice by accident.

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
from collections.abc import Iterable
from pathlib import Path

from app import jobs

logger = logging.getLogger(__name__)

_FILE = ".handoff_send_keys.json"
_KEEP = 500  # most recent sends kept; the media cache is cleared long before

_LOCK = threading.Lock()
_WRITING: dict[str, set[str]] = {}  # key -> job ids its send is writing


class SendInProgress(RuntimeError):
    """A send with this key, or with one of these clips, is still writing."""


class AlreadySent(RuntimeError):
    """One of these clips already went to RicePoster under another key."""

    def __init__(self, sent: dict) -> None:
        super().__init__(sent.get("batch_id"))
        self.sent = sent


def _path() -> Path:
    return jobs._ensure_work_root() / _FILE


def _load() -> dict[str, dict]:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def begin(key: str, job_ids: Iterable[str] = (), resend: bool = False) -> dict | None:
    """Return the batch ``key`` already wrote, or claim ``key`` for a new write.

    Raises ``SendInProgress`` while a send with ``key``, or with one of these
    clips, is writing, and ``AlreadySent`` when one of these clips already
    went under another key and ``resend`` (the reviewer's confirmation of a
    second send) is not set. A caller that gets None must call ``end(key)``.
    """
    clips = set(job_ids)
    with _LOCK:
        data = _load()
        done = data.get(key)
        if isinstance(done, dict):
            return done
        if key in _WRITING or any(clips & writing for writing in _WRITING.values()):
            raise SendInProgress(key)
        if not resend:
            for sent in reversed(list(data.values())):
                if isinstance(sent, dict) and clips.intersection(
                    sent.get("job_ids") or ()
                ):
                    raise AlreadySent(sent)
        _WRITING[key] = clips
        return None


def record(key: str, result: dict, job_ids: Iterable[str] = ()) -> None:
    """Remember the batch ``key`` wrote and its clips. A failure is logged, not
    raised: the batch is already written and its reply must still reach the
    page."""
    with _LOCK:
        data = _load()
        data.pop(key, None)
        data[key] = {
            "batch_id": result["batch_id"],
            "clip_count": result["clip_count"],
            "job_ids": sorted(set(job_ids)),
        }
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
        _WRITING.pop(key, None)
