"""A manual posting run in flight when the process dies (RiceSuite ADR-001 Q17).

Scheduled batches were already covered: the scheduler marks a batch `running`
before it posts, and the next startup sweep turns a stale `running` batch into
`interrupted` without ever re-executing it. A manual Post All run left no
durable trace, so a crash mid-run (or the RiceSuite supervisor restarting a
dead Poster) lost the fact that live posts might exist.

This marker closes that gap. It is written after the request is validated and
before any platform is touched, and removed once the run has ended in this
process. If it survives to the next startup, every slot of that run is
recorded in history as unconfirmed on each platform it targeted, and nothing
is retried: the maintainer checks the accounts before posting again.
"""

import datetime
import json
import os
import uuid
from pathlib import Path

from backend.config import DATA_ROOT

MARKER = DATA_ROOT / ".post-in-flight.json"

NOTE = (
    "RicePoster stopped while this manual run was in flight; the outcome is "
    "unknown and it was not retried. Check the account before posting again."
)


def begin(slots: list[dict], post_mode: str, headless: bool) -> None:
    record = {
        "run_id": uuid.uuid4().hex,
        "started": datetime.datetime.now(datetime.timezone.utc).isoformat(
            timespec="seconds"
        ),
        "post_mode": post_mode,
        "headless": headless,
        "slots": [
            {
                "slot": s["slot"],
                "file": Path(s["media_path"]).name,
                "caption": s.get("caption", ""),
                "platforms": sorted(s.get("enabled_platforms", ())),
            }
            for s in slots
        ],
    }
    tmp = MARKER.with_name(MARKER.name + ".tmp")
    tmp.write_text(json.dumps(record))
    os.replace(tmp, MARKER)


def end() -> None:
    MARKER.unlink(missing_ok=True)


def recover(history_file: Path) -> list[dict]:
    """Turn a surviving marker into unconfirmed history rows. Never posts.

    An unreadable marker is set aside as `*.corrupt` for inspection rather
    than blocking startup.
    """
    if not MARKER.is_file():
        return []
    try:
        record = json.loads(MARKER.read_text())
        slots = record["slots"]
    except (OSError, ValueError, KeyError, TypeError):
        os.replace(MARKER, MARKER.with_name(MARKER.name + ".corrupt"))
        return []
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rows = []
    for s in slots:
        platforms = set(s.get("platforms") or ())
        rows.append({
            "ts": now,
            "slot": s["slot"],
            "account_id": s["slot"],
            "run_id": record.get("run_id", ""),
            "file": s.get("file", ""),
            "media_bytes": 0,
            "caption": s.get("caption", ""),
            "post_mode": record.get("post_mode", ""),
            "headless": record.get("headless"),
            # Same id vocabulary as a post whose success element never
            # appeared: the UI and stats already classify it as unconfirmed.
            "ig_post_id": f"ig_post_unconfirmed_{s['slot']}" if "instagram" in platforms else "",
            "tt_post_id": f"tt_post_unconfirmed_{s['slot']}" if "tiktok" in platforms else "",
            "errors": [],
            "interrupted": NOTE,
        })
    with open(history_file, "a") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    MARKER.unlink(missing_ok=True)
    return rows
