"""Candidate-slice persistence + the incremental v1->v2 migration (D1)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from ricesearcher.library.store import MIGRATIONS, SCHEMA_VERSION, Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)


def _source(sid: str = "src1") -> Source:
    return Source(
        id=sid,
        kind=SourceKind.YOUTUBE,
        ref="https://youtu.be/x",
        media_path="/cache/x.mp4",
        words=[TranscriptWord("hi", 0.0, 0.2)],
    )


def _slice(sid: str, source_id: str = "src1", score: float = 0.5) -> CandidateSlice:
    return CandidateSlice(
        id=sid,
        source_id=source_id,
        pad_in=8.0,
        pad_out=42.0,
        target_in=10.0,
        target_out=40.0,
        transcript_span="a clippable moment",
        score=score,
        rationale="funny + on-beat",
        heuristic_score=0.7,
        heuristic_features={"keyword": 1.0, "question": 0.0},
        beat_profile_version="2026-09-03",
        scorer_model="fake",
        rights_risk="med",
        status=SliceStatus.CANDIDATE,
        created_at="2026-09-03T00:00:00Z",
    )


def test_fresh_db_is_at_current_version(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        assert lib._stored_version() == SCHEMA_VERSION == 2


def test_slice_roundtrip_and_features_json(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source())
        lib.upsert_slices([_slice("sl1")])
        got = lib.get_slice("sl1")
    assert got is not None
    assert got.target_in == 10.0 and got.pad_out == 42.0
    assert got.heuristic_features == {"keyword": 1.0, "question": 0.0}
    assert got.status is SliceStatus.CANDIDATE


def test_list_slices_filters_and_orders(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source("src1"))
        lib.upsert_source(_source("src2"))
        lib.upsert_slices(
            [
                _slice("a", "src1", score=0.2),
                _slice("b", "src1", score=0.9),
                _slice("c", "src2", score=0.5),
            ]
        )
        src1 = lib.list_slices(source_id="src1")
        assert [s.id for s in src1] == ["b", "a"]  # score DESC
        assert len(lib.list_slices()) == 3
        assert [s.id for s in lib.list_slices(status=SliceStatus.SELECTED)] == []


def test_upsert_replaces_existing_slice(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source())
        lib.upsert_slices([_slice("sl1", score=0.1)])
        lib.upsert_slices([_slice("sl1", score=0.8)])
        got = lib.get_slice("sl1")
        assert got is not None and got.score == 0.8
        assert len(lib.list_slices()) == 1


def test_v1_database_upgrades_in_place_to_v2(tmp_path: Path) -> None:
    # Build a Phase-1 (v1) database by hand, with data, then open it with Library.
    db = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executescript(MIGRATIONS[1])
    conn.execute("INSERT INTO meta(key, value) VALUES ('schema_version', '1')")
    conn.execute(
        "INSERT INTO sources (id, kind, ref, media_path) VALUES "
        "('old1', 'youtube', 'r', '/m.mp4')"
    )
    conn.commit()
    conn.close()

    # candidate_slices should not exist yet.
    conn = sqlite3.connect(db)
    tables_before = {
        r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    conn.close()
    assert "candidate_slices" not in tables_before

    with Library(db) as lib:
        assert lib._stored_version() == 2
        # Pre-existing data survived the upgrade.
        assert lib.get_source("old1") is not None
        # New table is usable.
        lib.upsert_slices([_slice("sl1", "old1")])
        assert len(lib.list_slices()) == 1
