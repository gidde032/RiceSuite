"""Schema v2 -> v3 migration: partition every slice by the legacy profile."""

from __future__ import annotations

import shutil
import sqlite3
import threading
from pathlib import Path

import pytest

from ricesearcher.library.store import LEGACY_PROFILE_ID, MIGRATIONS, Library
from tests.fixtures.make_v2_fixture import FIXTURE_PATH, FIXTURE_SLICES


def _new_id(old_id: str) -> str:
    source_id, _, span = old_id.partition(":")
    return f"{source_id}:{LEGACY_PROFILE_ID}:{span}"


def _open_fixture(tmp_path: Path) -> Path:
    db = tmp_path / "library.sqlite3"
    shutil.copyfile(FIXTURE_PATH, db)
    return db


def test_migration_upgrades_to_v3(tmp_path: Path) -> None:
    db = _open_fixture(tmp_path)
    with Library(db):
        pass  # opening runs the migration
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()["value"]
        assert version == "3"
        rows = conn.execute("SELECT * FROM candidate_slices ORDER BY id").fetchall()
    finally:
        conn.close()

    assert len(rows) == len(FIXTURE_SLICES)  # row count unchanged
    by_new_id = {r["id"]: r for r in rows}
    for original in FIXTURE_SLICES:
        new_id = _new_id(original["id"])
        row = by_new_id[new_id]  # every id rewritten to the new form
        assert row["profile_id"] == LEGACY_PROFILE_ID
        assert row["status"] == original["status"]  # status unchanged
        assert row["created_at"] == original["created_at"]  # created_at unchanged
        if original["dup_of"]:
            assert row["dup_of"] == _new_id(original["dup_of"])  # dup_of rewritten
        else:
            assert row["dup_of"] is None


def test_migration_preserves_lookup_by_new_id(tmp_path: Path) -> None:
    db = _open_fixture(tmp_path)
    with Library(db) as lib:
        new_id = _new_id("s1:5000-6000")
        got = lib.get_slice(new_id)
        assert got is not None
        assert got.id == new_id
        assert got.profile_id == LEGACY_PROFILE_ID
        assert got.status.value == "selected"


def test_migration_scopes_counts_to_legacy_profile(tmp_path: Path) -> None:
    db = _open_fixture(tmp_path)
    with Library(db) as lib:
        counts = lib.profile_counts()
        assert set(counts) == {LEGACY_PROFILE_ID}
        c = counts[LEGACY_PROFILE_ID]
        assert c["sources"] == 2
        # FB-1: `candidates` counts only rows awaiting review, not every status.
        assert c["candidates"] == sum(
            1 for s in FIXTURE_SLICES if s["status"] == "candidate"
        )
        assert c["selected"] == 1
        assert c["handed_off"] == 1
        assert len(lib.list_slices(profile_id=LEGACY_PROFILE_ID)) == len(FIXTURE_SLICES)
        assert lib.list_slices(profile_id="other") == []


def test_failed_migration_rolls_back_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = _open_fixture(tmp_path)
    original = MIGRATIONS[3]
    monkeypatch.setitem(MIGRATIONS, 3, original + "\nSELECT * FROM missing_table;")

    with pytest.raises(sqlite3.OperationalError):
        Library(db)

    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(candidate_slices)")
        }
        version = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema_version'"
        ).fetchone()[0]
    assert "profile_id" not in columns
    assert version == "2"

    monkeypatch.setitem(MIGRATIONS, 3, original)
    with Library(db) as lib:
        assert lib._stored_version() == 3


def test_concurrent_openers_serialize_migration(tmp_path: Path) -> None:
    db = _open_fixture(tmp_path)
    barrier = threading.Barrier(3)
    errors: list[BaseException] = []

    def open_library() -> None:
        barrier.wait()
        try:
            with Library(db):
                pass
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=open_library) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join()

    assert errors == []
    with Library(db) as lib:
        assert lib._stored_version() == 3
