"""Pure duplicate-annotation logic (FR-6, D6)."""

from __future__ import annotations

from ricesearcher.dedup.annotate import annotate_duplicates, cosine
from ricesearcher.models import CandidateSlice, SliceStatus


def _slice(
    sid: str,
    source_id: str,
    *,
    score: float = 0.5,
    ti: float = 0.0,
    to: float = 10.0,
    text: str = "x",
    status: SliceStatus = SliceStatus.CANDIDATE,
) -> CandidateSlice:
    return CandidateSlice(
        id=sid,
        source_id=source_id,
        pad_in=ti,
        pad_out=to,
        target_in=ti,
        target_out=to,
        transcript_span=text,
        score=score,
        status=status,
    )


def test_cosine_basics() -> None:
    assert cosine([1.0, 0.0], [1.0, 0.0]) == 1.0
    assert cosine([1.0, 0.0], [0.0, 1.0]) == 0.0
    assert cosine([], [1.0]) == 0.0
    assert cosine([0.0, 0.0], [1.0, 1.0]) == 0.0  # degenerate


def test_cross_source_embedding_duplicate() -> None:
    a = _slice("a", "src1", score=0.9, text="same moment")
    b = _slice("b", "src2", score=0.4, text="same moment")
    emb = {"a": [1.0, 0.0], "b": [1.0, 0.0]}  # identical -> cosine 1.0
    out = {s.id: s for s in annotate_duplicates([a, b], emb, sim_threshold=0.85)}
    # Higher-scored 'a' is canonical (clean); 'b' points at it.
    assert out["a"].dup_of is None
    assert out["b"].dup_of == "a"
    assert out["b"].dup_kind == "cross"
    assert out["b"].dup_score == 1.0


def test_no_cross_dup_below_threshold() -> None:
    a = _slice("a", "src1", text="alpha")
    b = _slice("b", "src2", text="beta")
    emb = {"a": [1.0, 0.0], "b": [0.0, 1.0]}  # orthogonal
    out = {s.id: s for s in annotate_duplicates([a, b], emb, sim_threshold=0.85)}
    assert out["a"].dup_of is None and out["b"].dup_of is None


def test_intra_source_time_overlap_duplicate() -> None:
    a = _slice("a", "src1", score=0.8, ti=10.0, to=30.0)
    b = _slice("b", "src1", score=0.5, ti=15.0, to=32.0)  # overlaps a heavily
    out = {s.id: s for s in annotate_duplicates([a, b], {}, overlap_threshold=0.5)}
    assert out["a"].dup_of is None
    assert out["b"].dup_of == "a"
    assert out["b"].dup_kind == "intra"


def test_advisory_never_drops_or_reorders() -> None:
    slices = [_slice("a", "s1", text="m"), _slice("b", "s2", text="m")]
    emb = {"a": [1.0], "b": [1.0]}
    out = annotate_duplicates(slices, emb)
    # Same count, same order as input.
    assert [s.id for s in out] == ["a", "b"]


def test_rerun_clears_stale_annotation() -> None:
    # A slice previously marked dup must be cleared if it's no longer similar.
    a = _slice("a", "s1", text="alpha")
    a.dup_of, a.dup_score, a.dup_kind = "old", 0.99, "cross"
    out = {s.id: s for s in annotate_duplicates([a], {"a": [1.0, 0.0]})}
    assert out["a"].dup_of is None
    assert out["a"].dup_score == 0.0
    assert out["a"].dup_kind == ""
