from tests._util import words

from render.ass import StyleConfig, _ass_time, build_ass


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
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    # 3 caption events (one active-word window each), no header.
    assert len(dialogues) == 3
    # Each event highlights exactly one word via an inline colour override.
    for line in dialogues:
        assert line.count("\\c&H") == 1


def test_active_word_window_is_contiguous():
    ws = words(("a", 0.0, 0.2), ("b", 0.5, 0.7))
    ass = build_ass(ws, duration=1.0)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    # First window ends where the second word starts (0.50), not at a's end.
    assert "0:00:00.00,0:00:00.50" in dialogues[0]


def test_captions_off_still_emits_header():
    ass = build_ass(words(("skip", 0.0, 0.3)), header="Look 🥹", captions_on=False, duration=2.0)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 1
    assert "Header" in dialogues[0]
    assert "Look 🥹" in dialogues[0]  # emoji passed through untouched


def test_header_spans_full_duration():
    ass = build_ass(words(("x", 0.0, 0.3)), header="Hook", duration=4.2)
    header = [l for l in ass.splitlines() if l.startswith("Dialogue: 1")][0]
    assert "0:00:00.00,0:00:04.20" in header


def test_newlines_become_ass_breaks():
    ass = build_ass([], header="line one\nline two", duration=1.0)
    assert "line one\\Nline two" in ass


def test_style_config_parameterised():
    style = StyleConfig(highlight_color="FF0000", font="Impact", caption_margin_v=200)
    ass = build_ass(words(("hi", 0.0, 0.3)), duration=1.0, style=style)
    assert "Impact" in ass
    assert ",200,1" in ass  # MarginV in the caption style line
