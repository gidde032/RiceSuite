"""Scoring (FR-4, D4): an LLM scores + explains each shortlisted candidate window
against the beat profile. The Anthropic adapter is imported lazily; the
prompt-building and response-parsing are pure and unit-tested."""

from ricesearcher.score.base import ScoredResult, Scorer

__all__ = ["ScoredResult", "Scorer"]
