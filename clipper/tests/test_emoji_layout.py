"""Caption emoji rows: anchoring, placement, and the safe-zone lift (#66).

Picks are stored against a global word index (the word's position in the
clip's word list), so editing word text keeps them: word timing is locked
(SPEC D4). A phrase shows the emoji of the first anchor it contains.
"""

import re
from dataclasses import replace

import pytest

from render.ass import (
    CAPTION_STYLE_NAMES,
    CAPTION_ZONE_PX,
    EMOJI_GAP,
    StyleConfig,
    build_ass,
    emoji_lift,
    emoji_row_height,
    layout_phrases,
    style_for_presets,
)
from tests._util import words


def _per_char(px: float = 50.0):
    return lambda text: len(text) * px


def _ten_words(texts=None):
    """Ten words, 0.3 s apart with no pauses: two phrases of five."""
    texts = texts or [f"w{i}" for i in range(10)]
    return words(*[(t, i * 0.3, i * 0.3 + 0.3) for i, t in enumerate(texts)])


def _layout(ws, picks, style=None, measure=None):
    return layout_phrases(
        ws, style or StyleConfig(), measure or _per_char(20), emoji=picks
    )


# --- anchoring ---------------------------------------------------------------


def test_a_pick_follows_its_word_index_through_a_text_edit():
    picks = {3: ("🔥",)}
    before = _layout(_ten_words(), picks)
    edited = _ten_words([f"w{i}" if i != 3 else "blazing" for i in range(10)])
    after = _layout(edited, picks)
    for layout in (before, after):
        assert layout[0].anchor == 3
        assert layout[0].emoji == ("🔥",)
        assert layout[1].anchor is None


def test_the_first_anchor_in_a_phrase_wins():
    layout = _layout(_ten_words(), {4: ("🔥",), 1: ("😂", "🙌")})
    assert layout[0].anchor == 1
    assert layout[0].emoji == ("😂", "🙌")


def test_a_regrouped_phrase_keeps_its_first_anchor():
    # Emptying word 2 drops it, so word 5 joins the first phrase. Both picks
    # now fall in that phrase, and the first one (word 3) wins.
    texts = [f"w{i}" for i in range(10)]
    texts[2] = "  "
    layout = _layout(_ten_words(texts), {3: ("🎉",), 5: ("🍕",)})
    assert [w.text for w in layout[0].phrase.words] == ["w0", "w1", "w3", "w4", "w5"]
    assert layout[0].anchor == 3
    assert layout[1].anchor is None


def test_a_pick_on_an_emptied_word_is_dropped():
    texts = [f"w{i}" for i in range(10)]
    texts[3] = ""
    layout = _layout(_ten_words(texts), {3: ("🔥",)})
    assert all(p.anchor is None for p in layout)


def test_a_pick_past_the_last_word_is_ignored():
    layout = _layout(_ten_words(), {99: ("🔥",)})
    assert all(p.anchor is None for p in layout)


# --- placement ---------------------------------------------------------------


def test_a_one_line_phrase_puts_the_row_above():
    ws = words(("short", 0.0, 0.5), ("line", 0.5, 1.0))
    (p,) = _layout(ws, {1: ("👀",)})
    assert len(p.lines) == 1
    assert p.row == "above"


@pytest.mark.parametrize(("anchor", "row"), [(0, "above"), (2, "above"), (3, "below")])
def test_a_two_line_phrase_puts_the_row_beside_the_anchor_line(anchor, row):
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    (p,) = _layout(ws, {anchor: ("🔥",)}, measure=_per_char(50))
    assert p.lines == [[0, 1, 2], [3, 4]]
    assert p.row == row


def test_the_row_sits_a_gap_away_from_the_text_block():
    style = StyleConfig()
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    (above,) = _layout(ws, {0: ("🔥",)}, style, _per_char(50))
    (below,) = _layout(ws, {4: ("🔥",)}, style, _per_char(50))
    height = emoji_row_height(style)
    assert above.row_box == (above.top - EMOJI_GAP - height, above.top - EMOJI_GAP)
    assert below.row_box == (
        below.bottom + EMOJI_GAP,
        below.bottom + EMOJI_GAP + height,
    )
    assert above.bottom - above.top == 2 * style.font_size


# --- the safe-zone lift ------------------------------------------------------


def test_emoji_lift_the_whole_clip_by_one_row_so_the_text_never_jumps():
    style = StyleConfig()
    ws = _ten_words()
    flat = _layout(ws, None, style)
    lifted = _layout(ws, {7: ("🔥",)}, style)
    lift = emoji_lift(style)
    assert lift == emoji_row_height(style) + EMOJI_GAP
    # Every phrase moves, including the one without emoji.
    for a, b in zip(flat, lifted, strict=True):
        assert b.bottom == a.bottom - lift
    assert flat[0].bottom == style.play_res_y - style.caption_margin_v


def test_no_picks_means_no_lift():
    style = StyleConfig()
    assert [p.bottom for p in _layout(_ten_words(), {}, style)] == [
        p.bottom for p in _layout(_ten_words(), None, style)
    ]


@pytest.mark.parametrize("name", CAPTION_STYLE_NAMES)
def test_the_caption_zone_covers_a_lifted_two_line_block_and_its_row(name):
    style = style_for_presets(name, "plain")
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    (p,) = _layout(ws, {0: ("🔥",)}, style, _per_char(style.font_size * 0.5))
    assert len(p.lines) == 2
    assert p.row == "above"
    assert p.row_box[0] >= style.play_res_y - CAPTION_ZONE_PX


# --- the ASS -----------------------------------------------------------------


def test_emoji_without_motion_keep_the_flat_look_at_the_lifted_position():
    style = StyleConfig()
    ws = _ten_words()
    ass = build_ass(
        ws, duration=3.0, style=style, measure=_per_char(20), emoji={2: ("🔥",)}
    )
    dialogues = [d for d in ass.splitlines() if d.startswith("Dialogue:")]
    assert len(dialogues) == 10  # one layer: no soft shadow without Motion
    lifted = _layout(ws, {2: ("🔥",)}, style)
    y = round((lifted[0].top + lifted[0].bottom) / 2)
    for d in dialogues:
        text = d.split(",", 9)[9]
        assert f"\\pos(540,{y})" in text
        assert "\\q2" in text
        assert "\\t(" not in text and "\\blur" not in text and "\\r" not in text
        assert re.search(r"\\c&H[0-9A-F]{6}&\}w\d\{\\c&HFFFFFF&\}", text)


def test_emoji_off_leaves_the_script_unchanged():
    ws = _ten_words()
    assert build_ass(ws, duration=3.0, emoji=None) == build_ass(ws, duration=3.0)
    assert build_ass(ws, duration=3.0, emoji={}) == build_ass(ws, duration=3.0)


def test_motion_with_emoji_lifts_the_animated_block_too():
    style = replace(StyleConfig(), motion=True)
    ws = _ten_words()
    plain = build_ass(ws, duration=3.0, style=style, measure=_per_char(20))
    lifted = build_ass(
        ws, duration=3.0, style=style, measure=_per_char(20), emoji={2: ("🔥",)}
    )
    y0 = int(re.search(r"\\pos\(540,(\d+)\)", plain).group(1))
    y1 = int(re.search(r"\\pos\(540,(\d+)\)", lifted).group(1))
    assert y0 - y1 == emoji_lift(style)
