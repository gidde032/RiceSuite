"""Build the committed schema-v2 library fixture for the v3 migration test.

Run from the repo root to regenerate the binary:

    python tests/fixtures/make_v2_fixture.py

The fixture holds two sources and slices in every ``SliceStatus``, plus an
intra-source ``dup_of``, a cross-source ``dup_of``, and a dangling ``dup_of``.
The migration test imports :data:`FIXTURE_SLICES` to assert that ids, ``dup_of``,
statuses, and ``created_at`` survive the v2 -> v3 upgrade.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ricesearcher.library.store import MIGRATIONS

FIXTURE_PATH = Path(__file__).resolve().parent / "library_v2.sqlite3"

# Two sources. Ids contain no ':' so the migration's substr surgery is exact.
FIXTURE_SOURCES = [
    {"id": "s1", "ref": "https://example.test/a", "media_path": "cache/s1.mp4"},
    {"id": "s2", "ref": "https://example.test/b", "media_path": "cache/s2.mp4"},
]

# Old-form slice ids: {source_id}:{in}-{out}. Every SliceStatus appears once,
# plus intra-source, cross-source, and dangling dup_of.
FIXTURE_SLICES = [
    {
        "id": "s1:1000-2000",
        "source_id": "s1",
        "status": "candidate",
        "dup_of": None,
        "created_at": "2026-09-01T00:00:00Z",
    },
    {
        "id": "s1:3000-4000",
        "source_id": "s1",
        "status": "reviewed",
        "dup_of": "s1:1000-2000",
        "created_at": "2026-09-01T00:00:01Z",
    },  # intra
    {
        "id": "s1:5000-6000",
        "source_id": "s1",
        "status": "selected",
        "dup_of": None,
        "created_at": "2026-09-01T00:00:02Z",
    },
    {
        "id": "s1:7000-8000",
        "source_id": "s1",
        "status": "handed_off",
        "dup_of": "s1:9999-9999",
        "created_at": "2026-09-01T00:00:03Z",
    },  # dangling
    {
        "id": "s2:1000-2000",
        "source_id": "s2",
        "status": "rejected",
        "dup_of": "s1:5000-6000",
        "created_at": "2026-09-01T00:00:04Z",
    },  # cross
    {
        "id": "s2:3000-4000",
        "source_id": "s2",
        "status": "candidate",
        "dup_of": None,
        "created_at": "2026-09-01T00:00:05Z",
    },
]


def build(path: Path = FIXTURE_PATH) -> Path:
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.executescript(MIGRATIONS[1])
        conn.executescript(MIGRATIONS[2])
        conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '2')")
        for src in FIXTURE_SOURCES:
            conn.execute(
                "INSERT INTO sources (id, kind, ref, media_path) "
                "VALUES (?, 'youtube', ?, ?)",
                (src["id"], src["ref"], src["media_path"]),
            )
        for s in FIXTURE_SLICES:
            conn.execute(
                """INSERT INTO candidate_slices
                   (id, source_id, pad_in, pad_out, target_in, target_out,
                    transcript_span, score, dup_of, status, created_at)
                   VALUES (?, ?, 0, 0, 1, 2, 'span', 0.5, ?, ?, ?)""",
                (s["id"], s["source_id"], s["dup_of"], s["status"], s["created_at"]),
            )
        conn.commit()
    finally:
        conn.close()
    return path


if __name__ == "__main__":
    out = build()
    print(f"wrote {out}")
