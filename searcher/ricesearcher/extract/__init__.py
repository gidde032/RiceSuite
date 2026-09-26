"""Extraction (FR-3): a cheap heuristic prefilter turns a transcript into a
shortlist of candidate windows so the LLM scorer never sees the whole transcript."""

from ricesearcher.extract.prefilter import prefilter

__all__ = ["prefilter"]
