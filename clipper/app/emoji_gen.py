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

# The reply lists one item per phrase, about 30 tokens each, so this fits
# about 400 phrases (some 2,000 words). A model that thinks spends from the
# same budget. The shared client's 60 s per-attempt timeout bounds a reply as
# well; a reply cut off at the budget is not valid JSON and fails (502).
MAX_TOKENS = 12000

# Thinking is off where the model accepts it (RiceSuite #82). With adaptive
# thinking the model reasoned through every phrase, about 80 tokens each, so a
# clip of some 500 words ran past the timeout or the budget: a 502 after
# minutes of retries. Writing one item per phrase does that work in the reply
# instead (measured: 600 words in 11 s). Claude Sonnet 5.5 and Opus 5.5 reject
# a disabled setting, so any other model keeps its default.
THINKING_OFF: dict[str, dict[str, str]] = {"claude-haiku-5-5": {"type": "disabled"}}

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
    '"you know", "I mean", "and so" or "like, um". A phrase with any noun, '
    "action, or feeling worth showing is not filler, even when the rest of "
    'it is: "and then my sister" gets 👧 on "sister". Skipping is rare: '
    "fewer than one phrase in ten in a typical clip. Anchor on "
    "the word that suggests the emoji, never on a filler or function word.\n\n"
    "Keep the emoji varied and specific to each phrase. Avoid stock reaction "
    "emoji such as 🔥 😂 💯 ✨ 👀 unless the word literally means that. An "
    f"emoji may appear at most {MAX_USES_PER_EMOJI} times in the whole clip, "
    f"and never again within {REPEAT_GAP_S:g} seconds of its last use. If the "
    "best emoji for a phrase was used too recently or too often, choose "
    "another fitting emoji instead of skipping the phrase.\n\n"
    "Go through the phrases in order and write one item for every phrase, "
    "numbered as in the list: the index of the anchor word, the anchor word "
    "itself copied exactly, and one or two emoji, or an empty emoji list for "
    "a filler phrase you skip. Answer with JSON only, in this shape: "
    '{"picks": [{"phrase": 1, "word": 2, "text": "pizza", "emoji": ["🍕"]}, '
    '{"phrase": 2, "word": 5, "text": "know", "emoji": []}]}.'
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


_VARIANT_MARKS = {"\ufe0e", "\ufe0f", *map(chr, range(0x1F3FB, 0x1F400))}


def _emoji_key(emoji: str) -> str:
    """One key per emoji: ❤️ and ❤︎, or 👍 and 👍🏽, count as the same emoji.

    Drops the presentation selectors and the skin-tone modifiers.
    """
    return "".join(c for c in emoji if c not in _VARIANT_MARKS)


def _space_repeats(
    candidates: dict[int, list[tuple[int, list[str]]]], phrases: list[IndexedPhrase]
) -> list[EmojiPick]:
    """Choose each phrase's pick under the repeat limits, in phrase order.

    ``candidates`` maps a phrase to its (anchor word, emoji) items. An emoji
    is skipped when it has already appeared ``MAX_USES_PER_EMOJI`` times, or
    when its last appearance began less than ``REPEAT_GAP_S`` seconds before
    this phrase, measured on the times the prompt shows (0.1 s). A duplicate
    in one item is skipped too, and the first two emoji left are kept. The
    earliest anchor with any emoji left wins; a phrase with none gets no pick.
    """
    uses: dict[str, int] = {}
    last_start: dict[str, float] = {}
    kept: list[EmojiPick] = []
    for n in sorted(candidates):
        start = round(phrases[n].start, 1)
        for word, emoji in sorted(candidates[n], key=lambda c: c[0]):
            chosen: list[str] = []
            for e in emoji:
                key = _emoji_key(e)
                if key in {_emoji_key(c) for c in chosen}:
                    continue
                if uses.get(key, 0) >= MAX_USES_PER_EMOJI:
                    continue
                if key in last_start and round(start - last_start[key], 1) < (
                    REPEAT_GAP_S
                ):
                    continue
                chosen.append(e)
                if len(chosen) == 2:
                    break
            if chosen:
                for e in chosen:
                    key = _emoji_key(e)
                    uses[key] = uses.get(key, 0) + 1
                    last_start[key] = start
                kept.append(EmojiPick(word=word, emoji=chosen))
                break
    return kept[:EMOJI_PICKS_MAX]


def _plain(text: str) -> str:
    """A word for comparison: lowercase, letters and digits only."""
    return "".join(c for c in text.casefold() if c.isalnum())


def _same_word(spoken: str, copied: str) -> bool:
    """``friend's`` and ``friends`` count as the copied ``friend``."""
    return spoken == copied or spoken == copied + "s" or copied == spoken + "s"


def _match(phrase: IndexedPhrase, word: int, text: Any) -> int | None:
    """The word in ``phrase`` the item means, or ``None``.

    With a copied ``text``, the matching word nearest the index; without one,
    the index itself when the phrase holds it.
    """
    if not isinstance(text, str):
        return word if any(i == word for i, _t in phrase.words) else None
    wanted = _plain(text)
    hits = [i for i, t in phrase.words if wanted and _same_word(_plain(t), wanted)]
    return min(hits, key=lambda i: abs(i - word)) if hits else None


def _anchor(
    item: dict[str, Any], phrases: list[IndexedPhrase], phrase_of: dict[int, int]
) -> tuple[int, int] | None:
    """The (phrase, word) an item anchors on, or ``None`` to drop it.

    The model miscounts word indices, often by one and across a phrase
    boundary, but copies the phrase number and the word reliably (measured on
    #82). So the stated ``phrase`` (1-based, as the prompt lists them) and the
    copied ``text`` decide; the index only picks between repeats of the word.
    Only an item with a ``text`` falls back to the phrase holding its index.
    A ``phrase`` that is present but not a listed number drops the item.
    """
    word, text = item.get("word"), item.get("text")
    if type(word) is not int:
        return None
    tries: list[int] = []
    if "phrase" in item:
        number = item["phrase"]
        if type(number) is not int or not 1 <= number <= len(phrases):
            return None
        tries.append(number - 1)
    own = phrase_of.get(word)
    if own is not None and own not in tries and (isinstance(text, str) or not tries):
        tries.append(own)
    for n in tries:
        at = _match(phrases[n], word, text)
        if at is not None:
            return n, at
    return None


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

    Each item is anchored by :func:`_anchor`; items it cannot place, with no
    valid emoji, or of the wrong shape are dropped.
    Each phrase then gets the earliest anchor that has emoji left under the
    repeat limits, with two emoji at most (:func:`_space_repeats`), and at
    most ``EMOJI_PICKS_MAX`` picks are kept so a render can accept them all. A
    reply with no JSON object holding a ``picks`` list raises
    :class:`EmojiGenerationError`.
    """
    data = _picks_object(reply or "")
    if data is None:
        raise EmojiGenerationError("the emoji reply was not the expected JSON")

    phrase_of = {i: n for n, phrase in enumerate(phrases) for i, _t in phrase.words}
    candidates: dict[int, list[tuple[int, list[str]]]] = {}
    for item in data["picks"]:
        if not isinstance(item, dict) or not isinstance(item.get("emoji"), list):
            continue
        anchored = _anchor(item, phrases, phrase_of)
        if anchored is None:
            continue
        valid = [e for e in item["emoji"] if isinstance(e, str) and is_emoji_cluster(e)]
        if valid:
            phrase, word = anchored
            candidates.setdefault(phrase, []).append((word, valid))
    return _space_repeats(candidates, phrases)


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
        thinking=THINKING_OFF.get(anthropic_text.model()),
        client=client,
    )
    return parse_picks(reply, phrases)
