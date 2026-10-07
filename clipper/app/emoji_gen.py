"""Sonnet-picked caption emoji (RiceSuite #66, SPEC §5.1).

One model call per clip, made only when the user clicks **✨ Suggest emoji**
(SPEC §3). It sends the caption phrases as ``transcribe.phrasing.group_words``
produces them, each word numbered with its global index in the clip's word
list, and nothing else: no frame, no file. The reply names, for a few phrases,
an anchor word and one or two emoji. Picks are kept against the word index, so
later text edits keep them, and the reviewer edits them before rendering.

The request goes through Clipper's one Anthropic call site,
``app.anthropic_text`` (ADR-001 fact 1).
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any

from app import anthropic_text
from app.models import EmojiPick
from render.text_image import is_emoji_cluster
from transcribe.phrasing import WordLike, group_words

EmojiConfigError = anthropic_text.TextConfigError
EmojiGenerationError = anthropic_text.TextGenerationError

# A reply for a long clip lists at most a few dozen picks.
MAX_TOKENS = 1024

SYSTEM_PROMPT = (
    "You add emoji to the burned-in captions of a short vertical video. You "
    "get the clip's caption phrases in order. Each word is shown with its "
    "index in square brackets.\n\n"
    "Pick emoji for only a few phrases: about one phrase in four, never more "
    "than one phrase in three, and none at all when nothing fits. Most phrases "
    "get no emoji. Choose a phrase only when one concrete word in it (a "
    "thing, a place, a feeling, or an action) has an obvious, literal emoji. "
    "Never anchor on filler or function words. Never pick two phrases in a row "
    "unless both are clearly about something visual.\n\n"
    "For each pick give the index of that word and one or two emoji. Answer "
    "with JSON only, in this shape: "
    '{"picks": [{"word": 12, "emoji": ["🍕"]}]}. '
    'An empty list, {"picks": []}, is a good answer.'
)

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def _indexed_phrases(words: Sequence[WordLike]) -> list[list[tuple[int, str]]]:
    """The caption phrases, each a list of (global word index, text)."""
    index_of = {id(w): i for i, w in enumerate(words)}
    return [
        [(index_of[id(w)], w.text.strip()) for w in phrase.words]
        for phrase in group_words(words)
    ]


def build_prompt(phrases: list[list[tuple[int, str]]]) -> str:
    lines = ["Caption phrases:"]
    for n, phrase in enumerate(phrases, start=1):
        lines.append(f"{n}. " + " ".join(f"[{i}] {text}" for i, text in phrase))
    return "\n".join(lines)


def parse_picks(reply: str, phrases: list[list[tuple[int, str]]]) -> list[EmojiPick]:
    """Validate the model's reply into picks, one per phrase at most.

    Items with an unknown word, no valid emoji, or the wrong shape are dropped;
    a third emoji is dropped; the earliest anchor in a phrase wins. A reply
    with no JSON object holding a ``picks`` list raises
    :class:`EmojiGenerationError`.
    """
    match = _JSON_RE.search(reply or "")
    try:
        data: Any = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("picks"), list):
        raise EmojiGenerationError("the emoji reply was not the expected JSON")

    phrase_of = {i: n for n, phrase in enumerate(phrases) for i, _t in phrase}
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
    return [best[n] for n in sorted(best)]


def suggest_emoji(
    words: Sequence[WordLike], *, client: Any | None = None
) -> list[EmojiPick]:
    """Ask the model for sparse caption emoji; returns the validated picks.

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
