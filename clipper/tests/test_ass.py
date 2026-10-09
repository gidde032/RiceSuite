from dataclasses import replace

import pytest

from render.ass import (
    CAPTION_STYLE_NAMES,
    HEADER_STYLE_NAMES,
    StyleConfig,
    _ass_time,
    build_ass,
    fallback_span,
    style_for_presets,
)
from tests._util import words


def test_time_formatting():
    assert _ass_time(0) == "0:00:00.00"
    assert _ass_time(61.5) == "0:01:01.50"
    assert _ass_time(3661.23) == "1:01:01.23"
    assert _ass_time(-5) == "0:00:00.00"


@pytest.mark.smoke
def test_has_required_sections():
    ass = build_ass(words(("hi", 0.0, 0.3)), duration=1.0)
    for section in ("[Script Info]", "[V4+ Styles]", "[Events]"):
        assert section in ass
    assert "PlayResX: 1080" in ass
    assert "PlayResY: 1920" in ass


def test_one_caption_event_per_word():
    ws = words(("one", 0.0, 0.3), ("two", 0.3, 0.6), ("three", 0.6, 0.9))
    ass = build_ass(ws, captions_on=True, duration=1.0)
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    # 3 caption events (one active-word window each), no header.
    assert len(dialogues) == 3
    # Each event highlights exactly one word via an inline colour override.
    for line in dialogues:
        assert line.count("\\c&H") == 1


def test_active_word_window_is_contiguous():
    ws = words(("a", 0.0, 0.2), ("b", 0.5, 0.7))
    ass = build_ass(ws, duration=1.0)
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    # First window ends where the second word starts (0.50), not at a's end.
    assert "0:00:00.00,0:00:00.50" in dialogues[0]


def test_captions_off_still_emits_the_fallback_header():
    ass = build_ass(
        words(("skip", 0.0, 0.3)),
        fallback_header="Look 🥹",
        captions_on=False,
        duration=2.0,
    )
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == 1
    assert "HeaderFallback" in dialogues[0]
    assert "Look 🥹" in dialogues[0]  # emoji passed through untouched


def test_fallback_header_spans_full_duration():
    ass = build_ass(words(("x", 0.0, 0.3)), fallback_header="Hook", duration=4.2)
    header = next(line for line in ass.splitlines() if line.startswith("Dialogue: 1"))
    assert "0:00:00.00,0:00:04.20" in header


def test_fallback_newlines_become_ass_breaks():
    ass = build_ass([], fallback_header="line one\nline two", duration=1.0)
    assert "line one\\Nline two" in ass


def test_style_config_parameterised():
    style = StyleConfig(highlight_color="FF0000", font="Impact", caption_margin_v=200)
    ass = build_ass(words(("hi", 0.0, 0.3)), duration=1.0, style=style)
    assert "Impact" in ass
    assert ",200,1" in ass  # MarginV in the caption style line


def test_visual_preset_catalog_lists_the_style_families_in_page_order():
    # RiceSuite #79: four colour families, then the standalone styles.
    assert CAPTION_STYLE_NAMES == (
        "montserrat",
        "montserrat_violet",
        "montserrat_sky",
        "montserrat_green",
        "punch",
        "punch_volt",
        "punch_red",
        "punch_blue",
        "pop",
        "pop_bubblegum",
        "pop_lime",
        "pop_fire",
        "neon",
        "neon_inverse",
        "friendly",
        "editorial",
        "lyric_block",
        "velvet_serif",
        "din_condensed",
    )
    assert HEADER_STYLE_NAMES == ("plain", "black_plate", "white_plate")

    default = style_for_presets()
    assert default.font == "Montserrat Black"
    assert default.highlight_color == "FFD60A"
    assert default.header_font_size == 42

    white_plate = style_for_presets("montserrat", "white_plate")
    assert white_plate.header_color == "FFFFFF"
    assert white_plate.header_outline_color == "000000"


def test_caption_presets_change_font_and_highlight_without_leaving_ass():
    friendly = style_for_presets("friendly", "plain")
    punch = style_for_presets("punch", "plain")

    assert friendly.font == "Avenir Next"
    assert friendly.highlight_color == "4DD4AC"
    assert punch.font == "Impact"
    assert punch.highlight_color == "FF3B81"

    ass = build_ass(words(("hi", 0.0, 0.3)), duration=1.0, style=punch)
    assert "Style: Caption,Impact,92" in ass
    assert "&H813BFF&" in ass


def test_lyric_presets_match_the_ratified_font_and_color_treatments():
    lyric_block = style_for_presets("lyric_block", "plain")
    velvet_serif = style_for_presets("velvet_serif", "plain")
    din_condensed = style_for_presets("din_condensed", "plain")

    assert (lyric_block.font, lyric_block.highlight_color, lyric_block.bold) == (
        "Avenir Next Condensed",
        "00E5FF",
        True,
    )
    # RiceSuite #79 made Velvet Serif bold, larger, and heavier-edged.
    assert (
        velvet_serif.font,
        velvet_serif.highlight_color,
        velvet_serif.bold,
        velvet_serif.font_size,
        velvet_serif.outline,
    ) == ("Bodoni 72", "FF3654", True, 96, 5)
    assert (din_condensed.font, din_condensed.highlight_color, din_condensed.bold) == (
        "DIN Condensed",
        "A8C7E8",
        True,
    )

    ass = build_ass(words(("little", 0.0, 0.4)), duration=1.0, style=din_condensed)
    assert "Style: Caption,DIN Condensed,100" in ass
    assert "&HE8C7A8&" in ass


def test_header_presets_share_compact_scale_and_plain_has_no_plate():
    styles = [style_for_presets("montserrat", name) for name in HEADER_STYLE_NAMES]
    assert [style.header_font_size for style in styles] == [42, 42, 42]
    assert [style.header_plate for style in styles] == ["none", "translucent", "solid"]

    black = style_for_presets("montserrat", "black_plate")
    assert (black.header_plate_color, black.header_plate_opacity) == ("000000", 75)
    assert black.header_outline == 0  # libass drew no edge inside the box
    white = style_for_presets("montserrat", "white_plate")
    assert (white.header_plate_color, white.header_outline) == ("FFFFFF", 2)
    assert all(style.header_padding == 16 for style in styles)
    assert all(style.header_plate_radius == 0 for style in styles)


def test_header_preset_fills_every_header_control():
    from render.ass import HEADER_LOOK_FIELDS, header_preset

    for name in HEADER_STYLE_NAMES:
        assert set(header_preset(name)) == set(HEADER_LOOK_FIELDS)
    assert header_preset("plain")["y"] == 210


def test_fallback_header_is_a_minimal_text_line_at_the_header_position():
    style = replace(
        style_for_presets("montserrat", "white_plate"),
        header_font_size=60,
        header_color="FFCC00",
        header_align="left",
        header_margin_v=500,
    )
    ass = build_ass(
        [], fallback_header="Hook", duration=1.0, style=style, fallback_family="Impact"
    )
    line = next(x for x in ass.splitlines() if x.startswith("Style: HeaderFallback,"))
    assert line.startswith("Style: HeaderFallback,Impact,60,&H0000CCFF,")
    # BorderStyle 1 (no plate), outline 2, no shadow, top-left, MarginV 500.
    assert line.endswith(",1,2,0,7,80,80,500,1")


@pytest.mark.parametrize("plate,padding,outline", [("solid", 40, 2), ("none", 0, 8)])
@pytest.mark.parametrize("y", [0, 1380])
def test_fallback_span_includes_the_background_and_outline(plate, padding, outline, y):
    style = replace(
        StyleConfig(),
        header_margin_v=y,
        header_font_size=60,
        header_plate=plate,
        header_padding=padding,
        header_outline=outline,
    )
    top, bottom = fallback_span("One\nTwo", style)
    edge = max(padding if plate != "none" else 0, outline)
    assert bottom - top == 120 + 2 * edge
    assert top >= 0
    assert bottom <= 1380


def test_fallback_text_and_background_have_the_same_fixed_position():
    import re

    ass = build_ass(
        words(("ordinary", 0.0, 0.5), ("people", 0.5, 1.0)),
        fallback_header="One\nTwo",
        duration=1.0,
        style=replace(StyleConfig(), header_plate="solid", header_padding=40),
    )
    events = [line for line in ass.splitlines() if ",HeaderFallback" in line]
    positions = [re.search(r"\\pos\((\d+),(\d+)\)", line) for line in events]
    assert len(positions) == 2
    assert all(positions), (
        "fixed positions keep libass from moving the plate for captions"
    )
    assert positions[0].groups() == positions[1].groups()
