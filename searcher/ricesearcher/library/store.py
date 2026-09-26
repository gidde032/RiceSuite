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
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)

SCHEMA_VERSION = 3

# The profile id every pre-v3 slice belongs to (ADR-002). The v3 migration
# stamps it on every row and folds it into the slice id.
LEGACY_PROFILE_ID = "example-beat"


def _canonical_media_path(media_path: str | Path) -> str:
    """Canonicalize a media path's lexical separators and ``.`` segments.

    This deliberately does not resolve symlinks, case, or ``..`` segments:
    those are filesystem policies outside the shared-reference contract.
    """
    return str(Path(media_path))


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

# v3 (ADR-002): partition slices by profile. Stamp the legacy id on every row and
# fold it into the slice id and dup_of, both of the form {source}:{in}-{out}.
MIGRATIONS[3] = f"""
    ALTER TABLE candidate_slices ADD COLUMN profile_id TEXT NOT NULL DEFAULT '';
    UPDATE candidate_slices SET profile_id = '{LEGACY_PROFILE_ID}';
    UPDATE candidate_slices
       SET id = source_id || ':{LEGACY_PROFILE_ID}:'
                || substr(id, length(source_id) + 2);
    UPDATE candidate_slices
       SET dup_of = substr(dup_of, 1, instr(dup_of, ':') - 1)
                    || ':{LEGACY_PROFILE_ID}:'
                    || substr(dup_of, instr(dup_of, ':') + 1)
     WHERE dup_of IS NOT NULL AND dup_of != '';
    CREATE INDEX IF NOT EXISTS idx_slices_profile_status
        ON candidate_slices(profile_id, status);
    """


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
        self._conn.commit()
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            current = self._stored_version()
            for version in sorted(MIGRATIONS):
                if current >= version:
                    continue
                statement = ""
                for line in MIGRATIONS[version].splitlines(keepends=True):
                    statement += line
                    if sqlite3.complete_statement(statement):
                        self._conn.execute(statement)
                        statement = ""
                if statement.strip():
                    raise sqlite3.OperationalError(
                        f"incomplete migration SQL for version {version}"
                    )
                self._conn.execute(
                    "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (str(version),),
                )
                current = version
            self._conn.commit()
        except BaseException:
            self._conn.rollback()
            raise

    @contextmanager
    def immediate_transaction(self) -> Iterator[None]:
        """Hold the database write lock across a multi-step state transition."""
        if self._conn.in_transaction:
            raise RuntimeError("cannot nest an immediate transaction")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._conn.rollback()
            raise
        else:
            self._conn.commit()

    # -- sources ----------------------------------------------------------

    def upsert_source(self, source: Source) -> None:
        """Insert or update a source and its transcript words (one txn).

        Updating the existing parent row in place preserves candidate-slice
        children, unlike SQLite's ``INSERT OR REPLACE`` which deletes the
        parent first and cascades to those children.
        """
        with self._conn:
            self._conn.execute(
                """INSERT INTO sources
                   (id, kind, ref, media_path, title, channel,
                    published_at, acquired_at, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET
                       kind = excluded.kind,
                       ref = excluded.ref,
                       media_path = excluded.media_path,
                       title = excluded.title,
                       channel = excluded.channel,
                       published_at = excluded.published_at,
                       acquired_at = excluded.acquired_at,
                       duration_s = excluded.duration_s""",
                (
                    source.id,
                    source.kind.value,
                    source.ref,
                    _canonical_media_path(source.media_path),
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

    def is_media_path_referenced(self, media_path: Path) -> bool:
        """Return whether a persisted source currently references ``media_path``.

        Reviewer lens: cache custody (HIGH). Failure cleanup must not unlink a
        newly-created path that another source has already adopted.

        Compare canonicalized values in Python so databases created before
        path canonicalization still protect shared media without a migration.
        """
        canonical = _canonical_media_path(media_path)
        return any(
            _canonical_media_path(row["media_path"]) == canonical
            for row in self._conn.execute("SELECT media_path FROM sources")
        )

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

    def slice_counts(self) -> dict[str, int]:
        """Map source id -> number of candidate slices, for the media page.

        A left-outer view isn't needed: sources with zero slices simply don't
        appear, and the media page defaults a missing id to 0.
        """
        return {
            r["source_id"]: r["n"]
            for r in self._conn.execute(
                "SELECT source_id, COUNT(*) AS n FROM candidate_slices "
                "GROUP BY source_id"
            )
        }

    def profile_counts(self) -> dict[str, dict[str, int]]:
        """Map profile id -> counts, for the ``profiles`` command and API.

        Each value has ``sources`` (distinct source ids over all rows),
        ``candidates`` (rows awaiting review, ``status = 'candidate'`` only),
        ``selected``, and ``handed_off``. Counts stay inside one profile; slices
        of other profiles never leak in. #23 (the profiles page) reuses this
        reading of ``candidates``.
        """
        rows = self._conn.execute(
            """SELECT profile_id,
                      COUNT(DISTINCT source_id) AS sources,
                      SUM(status = 'candidate') AS candidates,
                      SUM(status = 'selected') AS selected,
                      SUM(status = 'handed_off') AS handed_off
                 FROM candidate_slices
                GROUP BY profile_id"""
        ).fetchall()
        return {
            r["profile_id"]: {
                "sources": r["sources"],
                "candidates": r["candidates"] or 0,
                "selected": r["selected"] or 0,
                "handed_off": r["handed_off"] or 0,
            }
            for r in rows
        }

    def delete_source(self, source_id: str) -> str | None:
        """Delete a source and everything under it, returning its media_path.

        Full-purge semantics (maintainer-ratified 2026-09-10): the transcript
        words and every candidate slice cascade away via ``ON DELETE CASCADE``
        (foreign keys are ON), including slices a human has already selected.
        This is only ever reached from the deliberate media-management delete
        controls — never from the pipeline. Returns ``None`` if no such source
        so the caller can answer 404 without a second lookup.
        """
        with self._conn:
            row = self._conn.execute(
                "SELECT media_path FROM sources WHERE id = ?", (source_id,)
            ).fetchone()
            if row is None:
                return None
            self._conn.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        return row["media_path"]

    def delete_all_sources(self) -> int:
        """Purge every source (transcripts + slices cascade). Returns the count.

        Backs the media page's "clear the whole cache" control. Disk media is
        the caller's to wipe afterward (via ``MediaCache.clear``); this only
        clears the index.
        """
        with self._conn:
            cur = self._conn.execute("SELECT COUNT(*) AS n FROM sources")
            n = cur.fetchone()["n"]
            self._conn.execute("DELETE FROM sources")
        return n

    # -- candidate slices -------------------------------------------------

    def _upsert_slices(self, slices: list[CandidateSlice]) -> None:
        """Insert or replace slices within the caller's transaction."""
        self._conn.executemany(
            """INSERT OR REPLACE INTO candidate_slices
               (id, source_id, pad_in, pad_out, target_in, target_out,
                transcript_span, score, rationale, heuristic_score,
                heuristic_features, beat_profile_version, profile_id,
                scorer_model, dup_of, dup_score, dup_kind, rights_risk,
                status, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
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
                    s.profile_id,
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

    def upsert_slices(self, slices: list[CandidateSlice]) -> None:
        """Insert or replace scored candidate slices (one txn)."""
        with self._conn:
            self._upsert_slices(slices)

    def replace_candidate_slices(
        self,
        source_id: str,
        slices: list[CandidateSlice],
        *,
        profile_id: str,
    ) -> None:
        """Atomically replace a source's candidate rows within one profile.

        Reviewer lens: candidate data integrity (HIGH). The delete and insert
        share one transaction, so a failed replacement rolls back to the prior
        shortlist while human-touched rows remain untouched. ``profile_id`` is
        required: the delete is scoped to that source **and** profile, so rows of
        other profiles are never touched (ADR-002 partition).
        """
        with self._conn:
            self._conn.execute(
                "DELETE FROM candidate_slices "
                "WHERE source_id = ? AND status = 'candidate' AND profile_id = ?",
                (source_id, profile_id),
            )
            protected_ids = {
                row["id"]
                for row in self._conn.execute(
                    "SELECT id FROM candidate_slices "
                    "WHERE source_id = ? AND profile_id = ? AND status != 'candidate'",
                    (source_id, profile_id),
                )
            }
            self._upsert_slices([s for s in slices if s.id not in protected_ids])

    def update_duplicate_annotations(self, slices: list[CandidateSlice]) -> None:
        """Persist advisory duplicate fields without rewriting lifecycle state."""
        with self._conn:
            self._conn.executemany(
                "UPDATE candidate_slices "
                "SET dup_of = ?, dup_score = ?, dup_kind = ? "
                "WHERE id = ? AND profile_id = ?",
                [
                    (s.dup_of, s.dup_score, s.dup_kind, s.id, s.profile_id)
                    for s in slices
                ],
            )

    def list_slices(
        self,
        *,
        profile_id: str | None = None,
        source_id: str | None = None,
        status: SliceStatus | None = None,
    ) -> list[CandidateSlice]:
        clauses = []
        params: list[str] = []
        if profile_id is not None:
            clauses.append("profile_id = ?")
            params.append(profile_id)
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

    def delete_candidate_slices(self, source_id: str) -> None:
        """Delete a source's slices that are still in ``candidate`` status.

        Used before a re-score so a shrunk shortlist leaves no orphans; slices a
        human has moved past ``candidate`` (reviewed/selected/...) are untouched.

        Note (review finding C4, deferred): this can remove a slice whose id is
        still referenced by another slice's ``dup_of`` (there is no FK on
        ``dup_of``). The dangling reference is display-only and self-heals on the
        next ``dedup`` run, which recomputes annotations from scratch — so re-run
        ``dedup`` after a re-score.
        """
        with self._conn:
            self._conn.execute(
                "DELETE FROM candidate_slices "
                "WHERE source_id = ? AND status = 'candidate'",
                (source_id,),
            )

    def source_titles(self) -> dict[str, str]:
        """Map source id -> title, for cheap provenance display without a join."""
        return {
            r["id"]: r["title"]
            for r in self._conn.execute("SELECT id, title FROM sources")
        }

    def update_slice_status(self, slice_id: str, status: SliceStatus) -> bool:
        """Set only a slice's ``status`` (targeted UPDATE). Returns False if absent.

        A single-column UPDATE (not a full-row rewrite) so a concurrent window edit
        can't be clobbered by a stale read (review finding W1).
        """
        with self._conn:
            cur = self._conn.execute(
                "UPDATE candidate_slices SET status = ? "
                "WHERE id = ? AND status != 'handed_off'",
                (status.value, slice_id),
            )
        return cur.rowcount > 0

    def bulk_update_status(self, slice_ids: list[str], status: SliceStatus) -> None:
        """Set ``status`` on many slices in ONE transaction (all-or-nothing).

        Used by the handoff so a crash can't leave some slices marked and others
        not (which a retry would then re-deliver) — review finding H1.
        """

        def update() -> None:
            self._conn.executemany(
                "UPDATE candidate_slices SET status = ? WHERE id = ?",
                [(status.value, sid) for sid in slice_ids],
            )

        # Respect an enclosing explicit transaction (the handoff publishes its
        # manifest and marks the exact validated snapshot under one short lock).
        if self._conn.in_transaction:
            update()
        else:
            with self._conn:
                update()

    def update_slice_window(
        self, slice_id: str, target_in: float, target_out: float
    ) -> bool:
        """Set only a slice's intended in/out (targeted UPDATE). False if absent."""
        with self._conn:
            cur = self._conn.execute(
                "UPDATE candidate_slices SET target_in = ?, target_out = ? "
                "WHERE id = ? AND status != 'handed_off'",
                (target_in, target_out, slice_id),
            )
        return cur.rowcount > 0

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


def _coerce_status(value: str) -> SliceStatus:
    """Tolerate an unknown status (e.g. a newer DB read by older code) instead of
    letting one row raise and poison a whole ``list_slices`` result."""
    try:
        return SliceStatus(value)
    except ValueError:
        return SliceStatus.CANDIDATE


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
        profile_id=row["profile_id"],
        scorer_model=row["scorer_model"],
        dup_of=row["dup_of"],
        dup_score=row["dup_score"],
        dup_kind=row["dup_kind"],
        rights_risk=row["rights_risk"],
        status=_coerce_status(row["status"]),
        created_at=row["created_at"],
    )
