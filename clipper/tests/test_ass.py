from render.ass import (
    CAPTION_STYLE_NAMES,
    HEADER_STYLE_NAMES,
    StyleConfig,
    _ass_time,
    build_ass,
    style_for_presets,
)
from tests._util import words


def test_time_formatting():
    assert _ass_time(0) == "0:00:00.00"
    assert _ass_time(61.5) == "0:01:01.50"
    assert _ass_time(3661.23) == "1:01:01.23"
    assert _ass_time(-5) == "0:00:00.00"


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


def test_captions_off_still_emits_header():
    ass = build_ass(
        words(("skip", 0.0, 0.3)), header="Look 🥹", captions_on=False, duration=2.0
    )
    dialogues = [line for line in ass.splitlines() if line.startswith("Dialogue:")]
    assert len(dialogues) == 1
    assert "Header" in dialogues[0]
    assert "Look 🥹" in dialogues[0]  # emoji passed through untouched


def test_header_spans_full_duration():
    ass = build_ass(words(("x", 0.0, 0.3)), header="Hook", duration=4.2)
    header = next(line for line in ass.splitlines() if line.startswith("Dialogue: 1"))
    assert "0:00:00.00,0:00:04.20" in header


def test_newlines_become_ass_breaks():
    ass = build_ass([], header="line one\nline two", duration=1.0)
    assert "line one\\Nline two" in ass


def test_style_config_parameterised():
    style = StyleConfig(highlight_color="FF0000", font="Impact", caption_margin_v=200)
    ass = build_ass(words(("hi", 0.0, 0.3)), duration=1.0, style=style)
    assert "Impact" in ass
    assert ",200,1" in ass  # MarginV in the caption style line


def test_visual_preset_catalog_keeps_classic_and_exposes_requested_choices():
    assert CAPTION_STYLE_NAMES == (
        "classic",
        "clean",
        "punch",
        "friendly",
        "sunset",
        "mono",
        "editorial",
    )
    assert HEADER_STYLE_NAMES == ("plain", "black_plate", "white_plate")

    classic = style_for_presets("classic", "plain")
    assert classic.font == "Arial"
    assert classic.highlight_color == "35E36B"
    assert classic.header_font_size == 42

    white_plate = style_for_presets("classic", "white_plate")
    assert white_plate.header_color == "FFFFFF"
    assert white_plate.header_outline_color == "000000"


def test_caption_presets_change_font_and_highlight_without_leaving_ass():
    clean = style_for_presets("clean", "plain")
    punch = style_for_presets("punch", "plain")

    assert clean.font == "Helvetica Neue"
    assert clean.highlight_color == "F59E0B"
    assert punch.font == "Impact"
    assert punch.highlight_color == "FF3B81"

    ass = build_ass(words(("hi", 0.0, 0.3)), duration=1.0, style=punch)
    assert "Style: Caption,Impact,92" in ass
    assert "&H813BFF&" in ass


def test_header_presets_share_compact_scale_and_plain_has_no_plate():
    styles = [style_for_presets("classic", name) for name in HEADER_STYLE_NAMES]
    assert [style.header_font_size for style in styles] == [42, 42, 42]

    plain = build_ass(
        [], header="Hook", duration=1.0, style=style_for_presets("classic", "plain")
    )
    black = build_ass(
        [],
        header="Hook",
        duration=1.0,
        style=style_for_presets("classic", "black_plate"),
    )
    white = build_ass(
        [],
        header="Hook",
        duration=1.0,
        style=style_for_presets("classic", "white_plate"),
    )

    assert "Style: Header,Arial,42" in plain
    assert ",1,2,2,8,80,80,450,1" in plain
    assert ",3,16,0,8,80,80,450,1" in black
    assert ",3,16,0,8,80,80,450,1" in white
    assert "Style: HeaderPlate,Arial,42,&HFFFFFFFF,&HFFFFFFFF,&H00FFFFFF" in white
    assert "Style: Header,Arial,42,&H00FFFFFF,&H00FFFFFF,&H00000000" in white
    assert "Dialogue: 0,0:00:00.00,0:00:01.00,HeaderPlate" in white
    assert "Dialogue: 1,0:00:00.00,0:00:01.00,Header" in white
