"""Library-wide dedup annotation (FR-6) + the `dedup` CLI."""

from __future__ import annotations

from pathlib import Path

from ricesearcher import cli
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.pipeline import annotate_library_duplicates
from tests.conftest import FakeEmbedder


def _seed(lib: Library) -> None:
    lib.upsert_source(
        Source(id="s1", kind=SourceKind.YOUTUBE, ref="r", media_path="/m")
    )
    lib.upsert_source(
        Source(id="s2", kind=SourceKind.YOUTUBE, ref="r", media_path="/m")
    )
    lib.upsert_slices(
        [
            CandidateSlice(
                id="a",
                source_id="s1",
                pad_in=0,
                pad_out=10,
                target_in=0,
                target_out=10,
                transcript_span="the same clippable moment",
                score=0.9,
            ),
            CandidateSlice(
                id="b",
                source_id="s2",
                pad_in=0,
                pad_out=10,
                target_in=0,
                target_out=10,
                transcript_span="the same clippable moment",
                score=0.4,
            ),
            CandidateSlice(
                id="c",
                source_id="s2",
                pad_in=0,
                pad_out=10,
                target_in=50,
                target_out=60,
                transcript_span="a totally different thing",
                score=0.6,
            ),
        ]
    )


def test_annotate_library_flags_cross_source_dup(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        _seed(lib)
        annotate_library_duplicates(lib, fake_embedder)
        by_id = {s.id: s for s in lib.list_slices()}
    assert by_id["a"].dup_of is None  # higher-scored canonical
    assert by_id["b"].dup_of == "a" and by_id["b"].dup_kind == "cross"
    assert by_id["c"].dup_of is None  # distinct text


def test_annotate_never_removes_slices(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        _seed(lib)
        before = len(lib.list_slices())
        annotate_library_duplicates(lib, fake_embedder)
        annotate_library_duplicates(lib, fake_embedder)  # idempotent re-run
        after = lib.list_slices()
    assert len(after) == before == 3  # nothing dropped, ever


def test_annotate_preserves_human_status(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        _seed(lib)
        # Mark the duplicate 'b' as selected; dedup must not change its status.
        b = lib.get_slice("b")
        b.status = SliceStatus.SELECTED
        lib.upsert_slices([b])
        annotate_library_duplicates(lib, fake_embedder)
        assert lib.get_slice("b").status is SliceStatus.SELECTED
        assert lib.get_slice("b").dup_of == "a"  # still annotated, not filtered


def test_empty_library_dedup_is_noop(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        assert annotate_library_duplicates(lib, fake_embedder) == []


def test_cli_dedup_and_slices_marker(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", lambda: FakeEmbedder())
    from ricesearcher.config import load_config

    with Library(load_config().db_path) as lib:
        _seed(lib)

    assert cli.main(["dedup"]) == 0
    out = capsys.readouterr().out
    assert "flagged as possible duplicates" in out
    assert "advisory only" in out

    assert cli.main(["slices"]) == 0
    listing = capsys.readouterr().out
    assert "~cross" in listing  # the advisory marker shows in the listing
