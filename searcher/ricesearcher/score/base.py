"""Scorer protocol and result type."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow


@dataclass
class ScoredResult:
    """An LLM verdict for one candidate window."""

    score: float  # 0.0-1.0 clippability for the beat
    rationale: str


class Scorer(Protocol):
    @property
    def model_name(self) -> str:
        """Identifier recorded on each slice for provenance."""
        ...

    def score(
        self, windows: list[CandidateWindow], profile: BeatProfile
    ) -> list[ScoredResult]:
        """Return one result per window, in the same order."""
        ...
