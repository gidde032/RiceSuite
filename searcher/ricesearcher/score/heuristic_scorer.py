"""Offline heuristic scorer (#2).

Ranks candidates by the prefilter's own heuristic score, so the rest of the flow
(dedup, review, handoff) can run without an API key. It makes no network call and
imports nothing that could. Slices it scores record ``scorer_model =
"heuristic-offline"`` so their provenance shows they were never LLM-scored.
"""

from __future__ import annotations

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow
from ricesearcher.score.base import ScoredResult

OFFLINE_MODEL_NAME = "heuristic-offline"


def _rationale(window: CandidateWindow) -> str:
    features = ", ".join(f"{k} {round(v, 2):g}" for k, v in window.features.items())
    detail = f" ({features})" if features else ""
    return f"Offline heuristic prefilter score only; not LLM-scored{detail}."


class HeuristicScorer:
    """Score candidate windows with the prefilter heuristic, fully offline."""

    @property
    def model_name(self) -> str:
        return OFFLINE_MODEL_NAME

    def score(
        self, windows: list[CandidateWindow], profile: BeatProfile
    ) -> list[ScoredResult]:
        return [
            ScoredResult(
                score=max(0.0, min(1.0, w.heuristic_score)), rationale=_rationale(w)
            )
            for w in windows
        ]
