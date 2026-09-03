"""SQLite library index (SPEC §6).

Schema evolves through an **incremental migration path** keyed on
``schema_version``: each entry in ``MIGRATIONS`` is the DDL that brings the DB up
to that version. ``_migrate`` reads the stored version and applies only the
migrations newer than it, so a Phase-1 (v1) database upgrades in place to v2
without losing data. Fresh databases apply every migration in order.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)

SCHEMA_VERSION = 2

# version -> DDL that migrates the schema UP to that version.
MIGRATIONS: dict[int, str] = {
    1: """
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
    """,
    2: """
    CREATE TABLE IF NOT EXISTS candidate_slices (
        id                   TEXT PRIMARY KEY,
        source_id            TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
        pad_in               REAL NOT NULL,
        pad_out              REAL NOT NULL,
        target_in            REAL NOT NULL,
        target_out           REAL NOT NULL,
        transcript_span      TEXT NOT NULL DEFAULT '',
        score                REAL NOT NULL DEFAULT 0,
        rationale            TEXT NOT NULL DEFAULT '',
        heuristic_score      REAL NOT NULL DEFAULT 0,
        heuristic_features   TEXT NOT NULL DEFAULT '{}',
        beat_profile_version TEXT NOT NULL DEFAULT '',
        scorer_model         TEXT NOT NULL DEFAULT '',
        dup_of               TEXT,
        dup_score            REAL NOT NULL DEFAULT 0,
        dup_kind             TEXT NOT NULL DEFAULT '',
        rights_risk          TEXT NOT NULL DEFAULT 'med',
        status               TEXT NOT NULL DEFAULT 'candidate',
        created_at           TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_slices_source ON candidate_slices(source_id);
    CREATE INDEX IF NOT EXISTS idx_slices_score ON candidate_slices(score DESC);
    """,
}


class Library:
    """A SQLite-backed source/transcript/slice store. Use as a context manager."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._migrate()
        except Exception:
            # Don't leak the connection if setup fails (e.g. a corrupt db file):
            # the object is never fully constructed, so no caller can reach close().
            self._conn.close()
            raise

    def _stored_version(self) -> int:
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()
        return int(row["value"]) if row else 0

    def _migrate(self) -> None:
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        current = self._stored_version()
        for version in sorted(MIGRATIONS):
            if current < version:
                self._conn.executescript(MIGRATIONS[version])
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
                current = version
        self._conn.commit()

    # -- sources ----------------------------------------------------------

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
        # Literal prefix match: substr(...) avoids LIKE treating a '%' or '_' in
        # the prefix as a wildcard (real ids are hex, but the query must be
        # correct against its own contract regardless of input).
        rows = self._conn.execute(
            "SELECT id FROM sources WHERE substr(id, 1, length(?)) = ?",
            (prefix, prefix),
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

    # -- candidate slices -------------------------------------------------

    def upsert_slices(self, slices: list[CandidateSlice]) -> None:
        """Insert or replace scored candidate slices (one txn)."""
        with self._conn:
            self._conn.executemany(
                """INSERT OR REPLACE INTO candidate_slices
                   (id, source_id, pad_in, pad_out, target_in, target_out,
                    transcript_span, score, rationale, heuristic_score,
                    heuristic_features, beat_profile_version, scorer_model,
                    dup_of, dup_score, dup_kind, rights_risk, status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        s.id,
                        s.source_id,
                        s.pad_in,
                        s.pad_out,
                        s.target_in,
                        s.target_out,
                        s.transcript_span,
                        s.score,
                        s.rationale,
                        s.heuristic_score,
                        json.dumps(s.heuristic_features),
                        s.beat_profile_version,
                        s.scorer_model,
                        s.dup_of,
                        s.dup_score,
                        s.dup_kind,
                        s.rights_risk,
                        s.status.value,
                        s.created_at,
                    )
                    for s in slices
                ],
            )

    def list_slices(
        self, *, source_id: str | None = None, status: SliceStatus | None = None
    ) -> list[CandidateSlice]:
        clauses = []
        params: list[str] = []
        if source_id is not None:
            clauses.append("source_id = ?")
            params.append(source_id)
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn.execute(
            f"SELECT * FROM candidate_slices {where} ORDER BY score DESC, id",
            params,
        ).fetchall()
        return [_row_to_slice(r) for r in rows]

    def get_slice(self, slice_id: str) -> CandidateSlice | None:
        row = self._conn.execute(
            "SELECT * FROM candidate_slices WHERE id = ?", (slice_id,)
        ).fetchone()
        return _row_to_slice(row) if row is not None else None

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


def _row_to_slice(row: sqlite3.Row) -> CandidateSlice:
    return CandidateSlice(
        id=row["id"],
        source_id=row["source_id"],
        pad_in=row["pad_in"],
        pad_out=row["pad_out"],
        target_in=row["target_in"],
        target_out=row["target_out"],
        transcript_span=row["transcript_span"],
        score=row["score"],
        rationale=row["rationale"],
        heuristic_score=row["heuristic_score"],
        heuristic_features=json.loads(row["heuristic_features"]),
        beat_profile_version=row["beat_profile_version"],
        scorer_model=row["scorer_model"],
        dup_of=row["dup_of"],
        dup_score=row["dup_score"],
        dup_kind=row["dup_kind"],
        rights_risk=row["rights_risk"],
        status=SliceStatus(row["status"]),
        created_at=row["created_at"],
    )
