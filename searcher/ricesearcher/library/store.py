"""SQLite library index (SPEC §6).

Phase 1 persists sources and their word-level transcripts. The candidate-slice
tables (score, dedup, window, status) are added in Phase 2/3 via additive
migrations keyed on ``schema_version``.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ricesearcher.models import Source, SourceKind, TranscriptWord

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sources (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    ref          TEXT NOT NULL,
    media_path   TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    channel      TEXT NOT NULL DEFAULT '',
    published_at TEXT NOT NULL DEFAULT '',
    acquired_at  TEXT NOT NULL DEFAULT '',
    duration_s   REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transcript_words (
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    idx       INTEGER NOT NULL,
    text      TEXT NOT NULL,
    start     REAL NOT NULL,
    end       REAL NOT NULL,
    PRIMARY KEY (source_id, idx)
);
"""


class Library:
    """A SQLite-backed source/transcript store. Use as a context manager."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        self._conn.executescript(_SCHEMA)
        self._conn.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self._conn.commit()

    # -- writes -----------------------------------------------------------

    def upsert_source(self, source: Source) -> None:
        """Insert or replace a source and its transcript words (one txn)."""
        with self._conn:
            self._conn.execute(
                """INSERT OR REPLACE INTO sources
                   (id, kind, ref, media_path, title, channel,
                    published_at, acquired_at, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    source.id,
                    source.kind.value,
                    source.ref,
                    source.media_path,
                    source.title,
                    source.channel,
                    source.published_at,
                    source.acquired_at,
                    source.duration_s,
                ),
            )
            self._conn.execute(
                "DELETE FROM transcript_words WHERE source_id = ?", (source.id,)
            )
            self._conn.executemany(
                """INSERT INTO transcript_words (source_id, idx, text, start, end)
                   VALUES (?,?,?,?,?)""",
                [
                    (source.id, i, w.text, w.start, w.end)
                    for i, w in enumerate(source.words)
                ],
            )

    # -- reads ------------------------------------------------------------

    def get_source(self, source_id: str) -> Source | None:
        row = self._conn.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if row is None:
            return None
        words = [
            TranscriptWord(text=w["text"], start=w["start"], end=w["end"])
            for w in self._conn.execute(
                "SELECT text, start, end FROM transcript_words "
                "WHERE source_id = ? ORDER BY idx",
                (source_id,),
            )
        ]
        return _row_to_source(row, words)

    def resolve_source_id(self, prefix: str) -> str | None:
        """Resolve a full source id from an id prefix.

        Returns the id on a unique match, ``None`` on no match, and raises on an
        ambiguous prefix so the caller never acts on the wrong source.
        """
        rows = self._conn.execute(
            "SELECT id FROM sources WHERE id LIKE ? || '%'", (prefix,)
        ).fetchall()
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"ambiguous source id prefix: {prefix!r}")
        return rows[0]["id"]

    def list_sources(self) -> list[Source]:
        rows = self._conn.execute(
            "SELECT * FROM sources ORDER BY acquired_at DESC, id"
        ).fetchall()
        return [_row_to_source(r, []) for r in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Library:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def _row_to_source(row: sqlite3.Row, words: list[TranscriptWord]) -> Source:
    return Source(
        id=row["id"],
        kind=SourceKind(row["kind"]),
        ref=row["ref"],
        media_path=row["media_path"],
        title=row["title"],
        channel=row["channel"],
        published_at=row["published_at"],
        acquired_at=row["acquired_at"],
        duration_s=row["duration_s"],
        words=words,
    )
