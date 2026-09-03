"""Anthropic-backed scorer (FR-4, D4).

``build_prompt`` and ``parse_response`` are pure and unit-tested; only the network
call in ``score`` is lazy-imported and untested (``# pragma: no cover``). The
scorer only *reads* — it generates scores, it posts nothing.
"""

from __future__ import annotations

import json
import os

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow
from ricesearcher.score.base import ScoredResult

_DEFAULT_MODEL = "claude-sonnet-5"
_MODEL_ENV = "RICESEARCHER_SCORER_MODEL"


def build_prompt(windows: list[CandidateWindow], profile: BeatProfile) -> str:
    """Build the scoring prompt from the beat profile and the shortlist."""
    lines = [
        "You score short interview/podcast excerpts for how clippable they are "
        "for a specific social-media beat. Return ONLY a JSON array.",
        "",
        f"# Beat: {profile.name}",
        profile.brief,
    ]
    if profile.positive_examples:
        lines += ["", "## Good (clippable) examples:"]
        lines += [f"- {e}" for e in profile.positive_examples]
    if profile.negative_examples:
        lines += ["", "## Bad (not clippable) examples:"]
        lines += [f"- {e}" for e in profile.negative_examples]
    lines += [
        "",
        "# Candidates",
        "For each candidate below, output an object "
        '{"index": <int>, "score": <float 0-1>, "rationale": "<one sentence>"}.',
        'Return a JSON array of these objects and nothing else. "score" is how '
        "clippable the excerpt is for this beat (1 = a strong standalone clip, "
        "0 = not clippable).",
        "",
    ]
    for i, w in enumerate(windows):
        lines.append(f"[{i}] ({w.end - w.start:.0f}s) {w.text}")
    return "\n".join(lines)


def parse_response(text: str, n: int) -> list[ScoredResult]:
    """Parse the model's JSON array into ``n`` results, robust to surrounding text.

    Missing indices default to score 0; scores are clamped to [0, 1].
    """
    results = [ScoredResult(0.0, "") for _ in range(n)]
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1 or end < start:
        return results
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return results
    for item in parsed:
        if not isinstance(item, dict):
            continue
        idx = item.get("index")
        if not isinstance(idx, int) or not (0 <= idx < n):
            continue
        try:
            score = float(item.get("score", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        results[idx] = ScoredResult(
            score=max(0.0, min(1.0, score)),
            rationale=str(item.get("rationale", "")),
        )
    return results


class AnthropicScorer:
    """Score candidate windows with an Anthropic model."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or os.getenv(_MODEL_ENV) or _DEFAULT_MODEL

    @property
    def model_name(self) -> str:
        return self.model

    def score(
        self, windows: list[CandidateWindow], profile: BeatProfile
    ) -> list[ScoredResult]:  # pragma: no cover - live network path
        if not windows:
            return []
        import anthropic

        client = anthropic.Anthropic()
        message = client.messages.create(
            model=self.model,
            max_tokens=2048,
            messages=[{"role": "user", "content": build_prompt(windows, profile)}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return parse_response(text, len(windows))
