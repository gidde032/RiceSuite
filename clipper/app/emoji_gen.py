"""Model-picked caption emoji (RiceSuite #66, SPEC §5.1).

One model call per clip, made only when the user clicks **✨ Suggest emoji**
(SPEC §3). It sends the caption phrases as ``transcribe.phrasing.group_words``
produces them, each phrase with its start time and each word numbered with its
global index in the clip's word list, and nothing else: no frame, no file. The
reply names, for most phrases, an anchor word and one or two emoji (RiceSuite
#82). Repeats are capped and spaced here, after parsing, because the model
does not count reliably. Picks are kept against the word index, so later text
edits keep them, and the reviewer edits them before rendering.

The request goes through Clipper's one Anthropic call site,
``app.anthropic_text`` (ADR-001 fact 1).
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app import anthropic_text
from app.models import EMOJI_PICKS_MAX, EmojiPick
from render.text_image import is_emoji_cluster
from transcribe.phrasing import WordLike, group_words

EmojiConfigError = anthropic_text.TextConfigError
EmojiGenerationError = anthropic_text.TextGenerationError

# A reply now covers most phrases, so a long clip lists many picks. The model
# thinks first, and thinking counts against this budget too (RiceSuite #66).
# Kept under the SDK's non-streaming ceiling.
MAX_TOKENS = 12000

# Repeat limits (RiceSuite #82): one emoji appears at most this many times in
# a clip (so it repeats at most twice), and never again within this many
# seconds of its previous phrase.
MAX_USES_PER_EMOJI = 3
REPEAT_GAP_S = 8.0

SYSTEM_PROMPT = (
    "You add emoji to the burned-in captions of a short vertical video. You "
    "get the clip's caption phrases in order. Each phrase starts with its "
    "start time in seconds, and each word is shown with its index in square "
    "brackets.\n\n"
    "Give emoji to most phrases: aim for at least four phrases in five. Any "
    "phrase with a word that reasonably suggests a specific emoji gets one: a "
    "thing, a place, a person, an animal, a food, a feeling, an action, or an "
    "idea. Skip only phrases that are pure filler between statements, such as "
    '"you know", "I mean", "and so" or "like, um". Anchor on '
    "the word that suggests the emoji, never on a filler or function word.\n\n"
    "Keep the emoji varied and specific to each phrase. Prefer a fitting, "
    "less common emoji over a generic one you have already used. An emoji "
    f"may appear at most {MAX_USES_PER_EMOJI} times in the whole clip, and "
    f"never again within {REPEAT_GAP_S:g} seconds of its last use.\n\n"
    "For each pick give the index of that word and one or two emoji. Answer "
    "with JSON only, in this shape: "
    '{"picks": [{"word": 12, "emoji": ["🍕"]}]}.'
)

_DECODER = json.JSONDecoder()


@dataclass(frozen=True)
class IndexedPhrase:
    """A caption phrase: its start time and its (global word index, text)."""

    start: float
    words: list[tuple[int, str]]


def _indexed_phrases(words: Sequence[WordLike]) -> list[IndexedPhrase]:
    index_of = {id(w): i for i, w in enumerate(words)}
    return [
        IndexedPhrase(
            start=phrase.start,
            words=[(index_of[id(w)], w.text.strip()) for w in phrase.words],
        )
        for phrase in group_words(words)
    ]


def build_prompt(phrases: list[IndexedPhrase]) -> str:
    lines = ["Caption phrases:"]
    for n, phrase in enumerate(phrases, start=1):
        text = " ".join(f"[{i}] {t}" for i, t in phrase.words)
        lines.append(f"{n}. ({phrase.start:.1f}s) {text}")
    return "\n".join(lines)


def _emoji_key(emoji: str) -> str:
    """Compare ❤️ and ❤ as one emoji: drop the emoji-presentation selector."""
    return emoji.replace("\ufe0f", "")


def _space_repeats(
    picks: list[tuple[int, EmojiPick]], phrases: list[IndexedPhrase]
) -> list[EmojiPick]:
    """Apply the repeat limits in phrase order; a pick left empty is dropped.

    An emoji is dropped from a pick when it has already appeared
    ``MAX_USES_PER_EMOJI`` times, or when its last appearance began less than
    ``REPEAT_GAP_S`` seconds before this phrase. The same emoji twice in one
    pick keeps one.
    """
    uses: dict[str, int] = {}
    last_start: dict[str, float] = {}
    kept: list[EmojiPick] = []
    for n, pick in picks:
        start = phrases[n].start
        emoji: list[str] = []
        for e in pick.emoji:
            key = _emoji_key(e)
            if key in {_emoji_key(k) for k in emoji}:
                continue
            if uses.get(key, 0) >= MAX_USES_PER_EMOJI:
                continue
            if key in last_start and start - last_start[key] < REPEAT_GAP_S:
                continue
            emoji.append(e)
        for e in emoji:
            key = _emoji_key(e)
            uses[key] = uses.get(key, 0) + 1
            last_start[key] = start
        if emoji:
            kept.append(EmojiPick(word=pick.word, emoji=emoji))
    return kept[:EMOJI_PICKS_MAX]


def _picks_object(reply: str) -> dict[str, Any] | None:
    """The first JSON object in the reply that holds a ``picks`` list.

    Decoding starts at each ``{`` in turn and stops at the end of that object,
    so prose around the JSON may contain braces of its own.
    """
    start = reply.find("{")
    while start != -1:
        try:
            data, _end = _DECODER.raw_decode(reply, start)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("picks"), list):
            return data
        start = reply.find("{", start + 1)
    return None


def parse_picks(reply: str, phrases: list[IndexedPhrase]) -> list[EmojiPick]:
    """Validate the model's reply into picks, one per phrase at most.

    Items with an unknown word, no valid emoji, or the wrong shape are dropped;
    a third emoji is dropped; the earliest anchor in a phrase wins. Then the
    repeat limits apply (:func:`_space_repeats`), and at most
    ``EMOJI_PICKS_MAX`` picks are kept so a render can accept them all. A
    reply with no JSON object holding a ``picks`` list raises
    :class:`EmojiGenerationError`.
    """
    data = _picks_object(reply or "")
    if data is None:
        raise EmojiGenerationError("the emoji reply was not the expected JSON")

    phrase_of = {i: n for n, phrase in enumerate(phrases) for i, _t in phrase.words}
    best: dict[int, EmojiPick] = {}
    for item in data["picks"]:
        if not isinstance(item, dict):
            continue
        word, emoji = item.get("word"), item.get("emoji")
        if (
            type(word) is not int
            or word not in phrase_of
            or not isinstance(emoji, list)
        ):
            continue
        valid = [e for e in emoji if isinstance(e, str) and is_emoji_cluster(e)][:2]
        if not valid:
            continue
        phrase = phrase_of[word]
        if phrase not in best or word < best[phrase].word:
            best[phrase] = EmojiPick(word=word, emoji=valid)
    return _space_repeats([(n, best[n]) for n in sorted(best)], phrases)


def suggest_emoji(
    words: Sequence[WordLike], *, client: Any | None = None
) -> list[EmojiPick]:
    """Ask the model for caption emoji; returns the validated picks.

    Clips with no caption words send nothing and get no picks.
    """
    phrases = _indexed_phrases(words)
    if not phrases:
        return []
    (reply,) = anthropic_text.generate(
        purpose="emoji",
        system=SYSTEM_PROMPT,
        content=build_prompt(phrases),
        max_tokens=MAX_TOKENS,
        client=client,
    )
    return parse_picks(reply, phrases)
