"""SQLite library store (SPEC §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher.library.store import SCHEMA_VERSION, Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)


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


def test_media_path_reference_normalizes_new_and_existing_paths(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "cache" / "aa" / "file.mp4"
    equivalent = str(tmp_path / "cache") + "/aa//./file.mp4"

    with Library(tmp_path / "lib.sqlite3") as lib:
        normalized = _source("normalized")
        normalized.media_path = equivalent
        lib.upsert_source(normalized)

        stored = lib._conn.execute(
            "SELECT media_path FROM sources WHERE id = ?", (normalized.id,)
        ).fetchall()
        assert [row["media_path"] for row in stored] == [str(canonical)]
        assert lib.is_media_path_referenced(canonical)

        lib.delete_source(normalized.id)

        source = _source("first")
        source.media_path = str(canonical)
        lib.upsert_source(source)
        legacy = _source("legacy")
        legacy.media_path = equivalent
        lib._conn.execute(
            "INSERT INTO sources "
            "(id, kind, ref, media_path, title, channel, published_at, "
            "acquired_at, duration_s) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                legacy.id,
                legacy.kind.value,
                legacy.ref,
                legacy.media_path,
                legacy.title,
                legacy.channel,
                legacy.published_at,
                legacy.acquired_at,
                legacy.duration_s,
            ),
        )
        lib._conn.commit()
        lib.delete_source(source.id)
        assert lib.is_media_path_referenced(canonical)


def test_upsert_replaces_transcript(tmp_path: Path) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(_source(words=[TranscriptWord("old", 0.0, 0.1)]))
        lib.upsert_source(_source(words=[TranscriptWord("new", 0.0, 0.1)]))
        got = lib.get_source("abc123")
    assert got is not None
    assert got.transcript_text == "new"


def test_upsert_source_refresh_preserves_existing_slice_lifecycle(
    tmp_path: Path,
) -> None:
    """Library boundary (HIGH): refreshing a source must not delete its slices.

    Fix: update the source row in place instead of replacing it, preserving
    candidate-slice children while refreshing source and transcript data.
    """
    with Library(tmp_path / "lib.sqlite3") as lib:
        original = _source(words=[TranscriptWord("old", 0.0, 0.1)])
        lib.upsert_source(original)
        lib.upsert_slices(
            [
                CandidateSlice(
                    id=f"slice-{status.value}",
                    source_id=original.id,
                    pad_in=0.0,
                    pad_out=10.0,
                    target_in=1.0,
                    target_out=5.0,
                    transcript_span="old",
                    status=status,
                )
                for status in SliceStatus
            ]
        )

        refreshed = _source(words=[TranscriptWord("new", 0.0, 0.2)])
        refreshed.title = "Refreshed interview"
        refreshed.channel = "New channel"
        refreshed.duration_s = 200.0
        lib.upsert_source(refreshed)

        source = lib.get_source(original.id)
        slices = lib.list_slices(source_id=original.id)

    assert source is not None
    assert source.title == "Refreshed interview"
    assert source.channel == "New channel"
    assert source.duration_s == 200.0
    assert source.transcript_text == "new"
    assert {slice_.status for slice_ in slices} == set(SliceStatus)
    assert len(slices) == len(SliceStatus)


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
