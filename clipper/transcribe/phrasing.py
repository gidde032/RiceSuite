"""Group word-level transcript tokens into short caption phrases.

Pure standard library and side-effect free, so it is unit-testable without the
web stack or ffmpeg. Operates on duck-typed word objects exposing ``text``,
``start`` and ``end`` (both the pydantic ``Word`` model and plain dataclasses
satisfy this).

Phrasing is the caption "chunking" step of SPEC.md §5: a signature ~4-5 word
phrase block, within which each word is highlighted as it is spoken.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Protocol


class WordLike(Protocol):
    text: str
    start: float
    end: float


@dataclass
class Phrase:
    """A caption block: an ordered run of words shown together."""

    words: list

    @property
    def start(self) -> float:
        return self.words[0].start

    @property
    def end(self) -> float:
        return self.words[-1].end

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)


def group_words(
    words: Sequence[WordLike],
    *,
    max_words: int = 5,
    max_gap: float = 0.7,
) -> list[Phrase]:
    """Chunk ``words`` into phrase blocks.

    A new phrase starts when the current one reaches ``max_words`` or when the
    silent gap before the next word exceeds ``max_gap`` seconds (a natural pause,
    e.g. end of a sentence). Empty/whitespace-only tokens are dropped.
    """

    clean = [w for w in words if (w.text or "").strip()]
    if not clean:
        return []

    phrases: list[Phrase] = []
    current: list = [clean[0]]

    for prev, word in pairwise(clean):
        gap = word.start - prev.end
        if len(current) >= max_words or gap > max_gap:
            phrases.append(Phrase(current))
            current = [word]
        else:
            current.append(word)

    phrases.append(Phrase(current))
    return phrases
