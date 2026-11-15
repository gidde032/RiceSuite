"""Scripted model clients and transcripts for the #23 boundary tests.

No network: each client answers from a handler and records every prompt.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from ricesearcher.models import TranscriptWord
from ricesearcher.score.usage import CallUsage

_LINE = re.compile(r"^\[(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)\] ", re.M)
_PROPOSED = re.compile(r"The proposed moment is words w(\d+)-w(\d+)")
_WORD_ID = re.compile(r"^w(\d+) \[", re.M)


def words(n: int, *, step: float = 0.5, gap_every: int = 0, gap: float = 2.0):
    """``n`` words ``w0..`` of 0.4 s, ``step`` apart, with optional pauses."""
    out, t = [], 0.0
    for i in range(n):
        if gap_every and i and i % gap_every == 0:
            t += gap
        out.append(TranscriptWord(f"word{i}", round(t, 3), round(t + 0.4, 3)))
        t += step
    return out


def reference_span(prompt: str) -> tuple[float, float]:
    """The first and last reference times shown in a discovery prompt."""
    hits = _LINE.findall(prompt)
    return float(hits[0][0]), float(hits[-1][1])


def proposed_ids(prompt: str) -> tuple[int, int]:
    first, last = _PROPOSED.search(prompt).groups()  # type: ignore[union-attr]
    return int(first), int(last)


def context_ids(prompt: str) -> tuple[int, int]:
    ids = [int(i) for i in _WORD_ID.findall(prompt)]
    return min(ids), max(ids)


def moment(start: float, end: float, **extra) -> dict:
    return {"start": start, "end": end, "label": "topic", "strength": 2, **extra}


def refined(first: int, last: int, *, needs_more: bool = False, **extra) -> str:
    return json.dumps(
        {
            "first_word": first,
            "last_word": last,
            "needs_more_context": needs_more,
            "opening": "opens on the question",
            "setup": "setup present",
            "payoff": "payoff lands",
            "ending": "ends after the reaction",
            **extra,
        }
    )


def echo_discovery(prompt: str) -> str:
    """Propose one moment covering the shown references."""
    start, end = reference_span(prompt)
    return json.dumps([moment(start, end)])


def echo_refinement(prompt: str) -> str:
    """Confirm the proposed words unchanged."""
    return refined(*proposed_ids(prompt))


class ScriptedClient:
    """A ``ModelClient`` whose answers come from per-stage handlers.

    A handler returns text, or raises to simulate a failed call. Each call
    appends one ``CallUsage`` like the real client does.
    """

    def __init__(
        self,
        discovery: Callable[[str], str] = echo_discovery,
        refinement: Callable[[str], str] = echo_refinement,
        model_name: str = "fake-boundary-model",
    ) -> None:
        self.handlers = {"discovery": discovery, "refinement": refinement}
        self.model_name = model_name
        self.calls: list[tuple[str, str]] = []

    def complete(self, prompt: str, *, stage: str, usage_log: list[CallUsage]) -> str:
        self.calls.append((stage, prompt))
        record = CallUsage(stage=stage, model=self.model_name, input_tokens=1)
        usage_log.append(record)
        try:
            return self.handlers[stage](prompt)
        except Exception as exc:
            record.error = type(exc).__name__
            raise

    def prompts(self, stage: str) -> list[str]:
        return [p for s, p in self.calls if s == stage]


class Sequenced:
    """A handler that returns (or raises) each scripted answer in turn."""

    def __init__(self, *answers: object, then: Callable[[str], str] | None = None):
        self.answers = list(answers)
        self.then = then

    def __call__(self, prompt: str) -> str:
        if self.answers:
            answer = self.answers.pop(0)
        elif self.then is not None:
            return self.then(prompt)
        else:
            raise AssertionError("unexpected extra model call")
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer):
            return str(answer(prompt))
        return str(answer)


def reference_times(prompt: str) -> list[tuple[float, float]]:
    """Every ``[start-end]`` reference in a discovery prompt, in order."""
    return [(float(a), float(b)) for a, b in _LINE.findall(prompt)]


def covers(prompt: str) -> float:
    """The start second a discovery prompt says its transcript covers."""
    found = re.search(r"covers (\d+(?:\.\d+)?)s to", prompt)
    return float(found.group(1))  # type: ignore[union-attr]
