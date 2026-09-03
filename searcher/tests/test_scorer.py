"""Scorer prompt-building and response-parsing (FR-4) — the pure, testable half."""

from __future__ import annotations

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow
from ricesearcher.score.anthropic_scorer import (
    AnthropicScorer,
    build_prompt,
    parse_response,
)

_PROFILE = BeatProfile(
    version="v1",
    name="test-beat",
    brief="Clip the emotional beats.",
    positive_examples=["a warm reveal"],
    negative_examples=["logistics talk"],
)


def _windows(n: int) -> list[CandidateWindow]:
    return [
        CandidateWindow(
            source_id="s", start=i * 10.0, end=i * 10.0 + 8.0, text=f"line {i}"
        )
        for i in range(n)
    ]


def test_build_prompt_includes_brief_examples_and_indexed_candidates() -> None:
    prompt = build_prompt(_windows(2), _PROFILE)
    assert "Clip the emotional beats." in prompt
    assert "a warm reveal" in prompt
    assert "logistics talk" in prompt
    assert "[0]" in prompt and "[1]" in prompt
    assert "JSON array" in prompt


def test_parse_response_maps_by_index_and_clamps() -> None:
    text = (
        'Here you go: [{"index": 0, "score": 0.9, "rationale": "great"}, '
        '{"index": 1, "score": 1.7, "rationale": "over"}]'
    )
    results = parse_response(text, 2)
    assert results[0].score == 0.9 and results[0].rationale == "great"
    assert results[1].score == 1.0  # clamped to [0,1]


def test_parse_response_defaults_missing_indices_to_zero() -> None:
    results = parse_response('[{"index": 1, "score": 0.5, "rationale": "x"}]', 3)
    assert results[0].score == 0.0 and results[0].rationale == ""
    assert results[1].score == 0.5
    assert results[2].score == 0.0


def test_parse_response_tolerates_malformed_json() -> None:
    results = parse_response("no json here at all", 2)
    assert [r.score for r in results] == [0.0, 0.0]


def test_parse_response_ignores_out_of_range_index() -> None:
    results = parse_response('[{"index": 9, "score": 0.8, "rationale": "x"}]', 2)
    assert [r.score for r in results] == [0.0, 0.0]


def test_model_name_default_and_override(monkeypatch) -> None:
    monkeypatch.delenv("RICESEARCHER_SCORER_MODEL", raising=False)
    assert AnthropicScorer().model_name == "claude-sonnet-5"
    assert AnthropicScorer(model="claude-haiku-4-5-20251001").model_name == (
        "claude-haiku-4-5-20251001"
    )
    monkeypatch.setenv("RICESEARCHER_SCORER_MODEL", "env-model")
    assert AnthropicScorer().model_name == "env-model"
