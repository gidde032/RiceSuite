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

from ricesearcher.models import CandidateSlice, SliceStatus

# Defaults; tuned later against real data (the taste-spike sibling for dedup).
# Calibrated to 0.65 against real data (maintainer, 2026-09-03); overridable per-run
# via `dedup --threshold`. 0.85 was the initial conservative default, 0.5 too loose.
SIM_THRESHOLD = 0.65  # cosine similarity for a cross-source "possible duplicate"
OVERLAP_THRESHOLD = 0.5  # fraction of the shorter window that must overlap intra-source

# Canonical (the kept, clean member of a duplicate group) is chosen by status
# first — a human-kept slice should never be flagged as the duplicate of one the
# human rejected — then by score. Lower rank = preferred as canonical.
_STATUS_PRIORITY = {
    SliceStatus.SELECTED: 0,
    SliceStatus.HANDED_OFF: 0,
    SliceStatus.REVIEWED: 1,
    SliceStatus.CANDIDATE: 2,
    SliceStatus.REJECTED: 3,
}


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
    # Canonical order: kept status first, then highest score, then a deterministic
    # tie-break. The first slice of a duplicate group in this order is canonical.
    order = sorted(
        slices,
        key=lambda s: (_STATUS_PRIORITY.get(s.status, 2), -s.score, s.created_at, s.id),
    )
    cleared = {
        s.id: replace(s, dup_of=None, dup_score=0.0, dup_kind="") for s in slices
    }
    processed: list[CandidateSlice] = []
    root_of: dict[str, str] = {}  # slice id -> canonical (root) id of its group

    for s in order:
        match: tuple[CandidateSlice, float, str] | None = None
        # Compare against ALL already-processed slices (not just canonicals), so a
        # sliding-window duplicate that overlaps a *dup* — but not the group's
        # canonical — is still caught, then resolved to the canonical root.
        for p in processed:
            if s.source_id == p.source_id:
                frac = _overlap_fraction(s, p)
                if frac >= overlap_threshold:
                    match = (p, round(frac, 4), "intra")
                    break
            else:
                emb_s, emb_p = embeddings.get(s.id), embeddings.get(p.id)
                if emb_s and emb_p:
                    sim = cosine(emb_s, emb_p)
                    if sim >= sim_threshold:
                        match = (p, round(sim, 4), "cross")
                        break
        if match is None:
            root_of[s.id] = s.id  # s is its own canonical
        else:
            p, dup_score, dup_kind = match
            canonical_id = root_of[p.id]  # resolve to the group's root (no chains)
            root_of[s.id] = canonical_id
            r = cleared[s.id]
            r.dup_of, r.dup_score, r.dup_kind = canonical_id, dup_score, dup_kind
        processed.append(s)

    # Preserve the caller's original ordering.
    return [cleared[s.id] for s in slices]
