"""Pure duplicate-annotation logic (FR-6, D6).

Dependency-free and side-effect free so it is fully unit-testable without an
embedding model. Given the library's slices plus a (possibly partial) map of
transcript embeddings, it returns copies with the ``dup_of``/``dup_score``/
``dup_kind`` fields set. It **never** drops or reorders slices — every input slice
comes back, annotated or cleared.
"""

from __future__ import annotations

import math
from dataclasses import replace

from ricesearcher.models import CandidateSlice

# Defaults; tuned later against real data (the taste-spike sibling for dedup).
SIM_THRESHOLD = 0.85  # cosine similarity for a cross-source "possible duplicate"
OVERLAP_THRESHOLD = 0.5  # fraction of the shorter window that must overlap intra-source


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors (0.0 if either is degenerate)."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _overlap_fraction(a: CandidateSlice, b: CandidateSlice) -> float:
    """Overlap of two intended windows as a fraction of the shorter one."""
    inter = max(0.0, min(a.target_out, b.target_out) - max(a.target_in, b.target_in))
    shorter = min(a.target_out - a.target_in, b.target_out - b.target_in)
    return inter / shorter if shorter > 0 else 0.0


def annotate_duplicates(
    slices: list[CandidateSlice],
    embeddings: dict[str, list[float]],
    *,
    sim_threshold: float = SIM_THRESHOLD,
    overlap_threshold: float = OVERLAP_THRESHOLD,
) -> list[CandidateSlice]:
    """Return copies of ``slices`` with duplicate annotations set (advisory only).

    The highest-scored slice of a duplicate group is the canonical one (kept
    clean); the others point at it via ``dup_of``. Intra-source pairs match on
    time overlap (``dup_kind='intra'``); cross-source pairs on transcript-embedding
    cosine similarity (``dup_kind='cross'``). Nothing is removed or reordered.
    """
    # Canonical order: highest score first, then a deterministic tie-break.
    order = sorted(slices, key=lambda s: (-s.score, s.created_at, s.id))
    cleared = {
        s.id: replace(s, dup_of=None, dup_score=0.0, dup_kind="") for s in slices
    }
    canonicals: list[CandidateSlice] = []

    for s in order:
        match: tuple[str, float, str] | None = None
        for c in canonicals:
            if s.source_id == c.source_id:
                frac = _overlap_fraction(s, c)
                if frac >= overlap_threshold:
                    match = (c.id, round(frac, 4), "intra")
                    break
            else:
                emb_s, emb_c = embeddings.get(s.id), embeddings.get(c.id)
                if emb_s and emb_c:
                    sim = cosine(emb_s, emb_c)
                    if sim >= sim_threshold:
                        match = (c.id, round(sim, 4), "cross")
                        break
        if match is None:
            canonicals.append(s)
        else:
            dup_of, dup_score, dup_kind = match
            r = cleared[s.id]
            r.dup_of, r.dup_score, r.dup_kind = dup_of, dup_score, dup_kind

    # Preserve the caller's original ordering.
    return [cleared[s.id] for s in slices]
