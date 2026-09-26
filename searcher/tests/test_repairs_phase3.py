"""Fail-before-fix regressions for the PR #12 review findings (C1, C2, C3)."""

from __future__ import annotations

from pathlib import Path

from ricesearcher import cli
from ricesearcher.dedup.annotate import annotate_duplicates
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from tests.conftest import FakeEmbedder

PROFILE = "p1"


def _slice(sid, src, *, score=0.5, ti=0.0, to=10.0, status=SliceStatus.CANDIDATE):
    return CandidateSlice(
        id=sid,
        source_id=src,
        pad_in=ti,
        pad_out=to,
        target_in=ti,
        target_out=to,
        transcript_span="x",
        score=score,
        status=status,
        profile_id=PROFILE,
    )


# -- C1: a kept (selected) slice is canonical over a higher-scored rejected one --


def test_c1_selected_is_canonical_over_rejected() -> None:
    rej = _slice("rej", "s1", score=0.95, ti=10, to=30, status=SliceStatus.REJECTED)
    sel = _slice("sel", "s1", score=0.5, ti=15, to=32, status=SliceStatus.SELECTED)
    out = {s.id: s for s in annotate_duplicates([rej, sel], {})}
    assert out["sel"].dup_of is None  # the kept slice owns the group
    assert out["rej"].dup_of == "sel"


# -- C2: sliding-window duplicate is linked to the group's canonical ----------


def test_c2_sliding_window_links_to_root() -> None:
    a = _slice("a", "s1", score=0.9, ti=0, to=10)
    b = _slice("b", "s1", score=0.8, ti=5, to=15)  # overlaps a
    c = _slice("c", "s1", score=0.7, ti=10, to=20)  # overlaps b, not a
    out = {s.id: s for s in annotate_duplicates([a, b, c], {}, overlap_threshold=0.5)}
    assert out["a"].dup_of is None
    assert out["b"].dup_of == "a"
    assert out["c"].dup_of == "a"  # linked via b, resolved to the root canonical


def test_c2_no_chaining_dup_points_at_root_not_at_a_dup() -> None:
    a = _slice("a", "s1", score=0.9, ti=0, to=10)
    b = _slice("b", "s1", score=0.8, ti=5, to=15)
    c = _slice("c", "s1", score=0.7, ti=10, to=20)
    out = {s.id: s for s in annotate_duplicates([a, b, c], {}, overlap_threshold=0.5)}
    # No dup points at another dup.
    dups = {s.id: s.dup_of for s in out.values() if s.dup_of}
    for target in dups.values():
        assert out[target].dup_of is None


# -- C3: the slices listing keeps columns aligned with/without a dup marker ----


def test_c3_slices_columns_aligned(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    from ricesearcher.config import load_config

    with Library(load_config().db_path) as lib:
        lib.upsert_source(
            Source(id="s1", kind=SourceKind.LOCAL, ref="r", media_path="/m", title="T")
        )
        clean = _slice("clean", "s1", score=0.9, ti=0, to=10)
        dup = _slice("dup", "s1", score=0.5, ti=1, to=9)
        dup.dup_of, dup.dup_score, dup.dup_kind = "clean", 0.8, "intra"
        lib.upsert_slices([clean, dup])

    assert cli.main(["slices", "--profile", PROFILE]) == 0
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "'x'" in ln]
    assert len(lines) == 2
    # The transcript token 'x' must start at the same column on both rows.
    assert lines[0].index("'x'") == lines[1].index("'x'")


def test_c3_marker_still_shows(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    from ricesearcher.config import load_config
    from ricesearcher.pipeline import annotate_library_duplicates

    with Library(load_config().db_path) as lib:
        lib.upsert_source(
            Source(id="s1", kind=SourceKind.LOCAL, ref="r", media_path="/m")
        )
        lib.upsert_source(
            Source(id="s2", kind=SourceKind.LOCAL, ref="r", media_path="/m")
        )
        lib.upsert_slices(
            [
                _slice("a", "s1", score=0.9),
                CandidateSlice(
                    id="b",
                    source_id="s2",
                    pad_in=0,
                    pad_out=10,
                    target_in=0,
                    target_out=10,
                    transcript_span="x",
                    score=0.4,
                    profile_id=PROFILE,
                ),
            ]
        )
        # give a and b the same span so the fake embedder marks them dup
        a = lib.get_slice("a")
        a.transcript_span = "x"
        lib.upsert_slices([a])
        annotate_library_duplicates(lib, FakeEmbedder(), profile_id=PROFILE)
    assert cli.main(["slices", "--profile", PROFILE]) == 0
    assert "~cross" in capsys.readouterr().out
