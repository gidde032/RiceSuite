"""SQLite library store (SPEC §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher.library.store import SCHEMA_VERSION, Library
from ricesearcher.models import Source, SourceKind, TranscriptWord


def _source(sid: str = "abc123", words: list[TranscriptWord] | None = None) -> Source:
    return Source(
        id=sid,
        kind=SourceKind.YOUTUBE,
        ref="https://youtu.be/x",
        media_path="/cache/ab/abc123.mp4",
        title="Interview",
        channel="Chan",
        acquired_at="2026-09-02T00:00:00Z",
        duration_s=100.0,
        words=words or [TranscriptWord("hi", 0.0, 0.3)],
    )


def test_migrate_sets_schema_version(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        row = lib._conn.execute(
            "SELECT value FROM meta WHERE key='schema_version'"
        ).fetchone()
    assert row["value"] == str(SCHEMA_VERSION)


def test_upsert_and_get_roundtrip(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = _source()
        lib.upsert_source(src)
        got = lib.get_source("abc123")
    assert got is not None
    assert got.title == "Interview"
    assert got.transcript_text == "hi"
    assert got.kind is SourceKind.YOUTUBE


def test_upsert_replaces_transcript(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source(words=[TranscriptWord("old", 0.0, 0.1)]))
        lib.upsert_source(_source(words=[TranscriptWord("new", 0.0, 0.1)]))
        got = lib.get_source("abc123")
    assert got is not None
    assert got.transcript_text == "new"


def test_get_missing_returns_none(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        assert lib.get_source("missing") is None


def test_list_sources_orders_recent_first(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        a = _source("aaa")
        a.acquired_at = "2026-09-01T00:00:00Z"
        b = _source("bbb")
        b.acquired_at = "2026-09-02T00:00:00Z"
        lib.upsert_source(a)
        lib.upsert_source(b)
        ids = [s.id for s in lib.list_sources()]
    assert ids == ["bbb", "aaa"]


def test_resolve_source_id_prefix(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source("abcdef123456"))
        assert lib.resolve_source_id("abcdef") == "abcdef123456"
        assert lib.resolve_source_id("zzz") is None


def test_resolve_ambiguous_prefix_raises(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source("abc111"))
        lib.upsert_source(_source("abc222"))
        with pytest.raises(ValueError):
            lib.resolve_source_id("abc")
