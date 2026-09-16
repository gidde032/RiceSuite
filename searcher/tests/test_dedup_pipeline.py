"""Library-wide dedup annotation (FR-6) + the `dedup` CLI."""

from __future__ import annotations

from pathlib import Path

from ricesearcher import cli
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.pipeline import annotate_library_duplicates
from tests.conftest import FakeEmbedder

PROFILE = "p1"


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
                profile_id=PROFILE,
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
                profile_id=PROFILE,
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
                profile_id=PROFILE,
            ),
        ]
    )


def test_annotate_library_flags_cross_source_dup(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        _seed(lib)
        annotate_library_duplicates(lib, fake_embedder, profile_id=PROFILE)
        by_id = {s.id: s for s in lib.list_slices()}
    assert by_id["a"].dup_of is None  # higher-scored canonical
    assert by_id["b"].dup_of == "a" and by_id["b"].dup_kind == "cross"
    assert by_id["c"].dup_of is None  # distinct text


def test_annotate_never_removes_slices(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        _seed(lib)
        before = len(lib.list_slices())
        annotate_library_duplicates(lib, fake_embedder, profile_id=PROFILE)
        annotate_library_duplicates(
            lib, fake_embedder, profile_id=PROFILE
        )  # idempotent
        after = lib.list_slices()
    assert len(after) == before == 3  # nothing dropped, ever


def test_annotate_preserves_human_status(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        _seed(lib)
        # Mark the duplicate 'b' as selected; dedup must not change its status.
        b = lib.get_slice("b")
        b.status = SliceStatus.SELECTED
        lib.upsert_slices([b])
        annotate_library_duplicates(lib, fake_embedder, profile_id=PROFILE)
        assert lib.get_slice("b").status is SliceStatus.SELECTED  # status preserved
        # The kept (selected) slice 'b' is now the canonical; the candidate 'a' is
        # flagged as its duplicate — the group is still detected, never filtered.
        assert lib.get_slice("b").dup_of is None
        assert lib.get_slice("a").dup_of == "b"
        assert len(lib.list_slices()) == 3  # nothing dropped


def test_annotate_does_not_resurrect_concurrent_handoff(tmp_path: Path) -> None:
    db = tmp_path / "l.sqlite3"
    with Library(db) as lib:
        _seed(lib)

        class HandoffDuringEmbed(FakeEmbedder):
            def embed(self, texts: list[str]) -> list[list[float]]:
                with Library(db) as other:
                    other.bulk_update_status(["b"], SliceStatus.HANDED_OFF)
                return super().embed(texts)

        annotate_library_duplicates(lib, HandoffDuringEmbed(), profile_id=PROFILE)
        assert lib.get_slice("b").status is SliceStatus.HANDED_OFF


def test_empty_library_dedup_is_noop(tmp_path: Path, fake_embedder) -> None:
    with Library(tmp_path / "l.sqlite3") as lib:
        assert annotate_library_duplicates(lib, fake_embedder, profile_id=PROFILE) == []


def test_cli_dedup_and_slices_marker(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", lambda: FakeEmbedder())
    from ricesearcher.config import load_config

    with Library(load_config().db_path) as lib:
        _seed(lib)

    assert cli.main(["dedup", "--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    assert "flagged as possible duplicates" in out
    assert "advisory only" in out

    assert cli.main(["slices", "--profile", PROFILE]) == 0
    listing = capsys.readouterr().out
    assert "~cross" in listing  # the advisory marker shows in the listing


def test_dedup_output_is_human_readable(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", lambda: FakeEmbedder())
    from ricesearcher.config import load_config

    with Library(load_config().db_path) as lib:
        lib.upsert_source(
            Source(
                id="s1",
                kind=SourceKind.YOUTUBE,
                ref="r",
                media_path="/m",
                title="Person A on Fallon",
            )
        )
        lib.upsert_source(
            Source(
                id="s2",
                kind=SourceKind.YOUTUBE,
                ref="r",
                media_path="/m",
                title="Person B interview reupload",
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="a",
                    source_id="s1",
                    pad_in=0,
                    pad_out=10,
                    target_in=30,
                    target_out=48,
                    transcript_span="the exact same funny moment",
                    score=0.9,
                    profile_id=PROFILE,
                ),
                CandidateSlice(
                    id="b",
                    source_id="s2",
                    pad_in=0,
                    pad_out=10,
                    target_in=61,
                    target_out=79,
                    transcript_span="the exact same funny moment",
                    score=0.4,
                    profile_id=PROFILE,
                ),
            ]
        )
    assert cli.main(["dedup", "--profile", PROFILE]) == 0
    out = capsys.readouterr().out
    # Shows titles, the window, and the transcript snippet — not raw id prefixes.
    assert "Person A on Fallon" in out
    assert "Person B interview reupload" in out
    assert "the exact same funny moment" in out
    assert "61-79s" in out  # the duped clip's window


def test_dedup_threshold_flag_is_honored(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", lambda: FakeEmbedder())
    from ricesearcher.config import load_config

    with Library(load_config().db_path) as lib:
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
                    transcript_span="same",
                    score=0.9,
                    profile_id=PROFILE,
                ),
                CandidateSlice(
                    id="b",
                    source_id="s2",
                    pad_in=0,
                    pad_out=10,
                    target_in=0,
                    target_out=10,
                    transcript_span="same",
                    score=0.4,
                    profile_id=PROFILE,
                ),
            ]
        )
    # Identical span -> cosine 1.0. A threshold above 1.0 flags nothing.
    assert cli.main(["dedup", "--profile", PROFILE, "--threshold", "1.5"]) == 0
    assert "0 of 2" in capsys.readouterr().out
    # The default (0.65) flags the pair.
    assert cli.main(["dedup", "--profile", PROFILE]) == 0
    assert "1 of 2" in capsys.readouterr().out
