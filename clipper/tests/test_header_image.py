import pytest

from render import header_image
from render.ass import style_for_presets
from render.header_image import _segment, _wrap, has_emoji


def _fake_measure(word):
    # width == character count; parts unused by _wrap
    return (word, float(len(word)))


def test_wrap_honors_explicit_newlines_as_hard_breaks():
    # Regression: the emoji path split on " " only, mangling 2-line headers.
    lines = _wrap("line one\nline two", _fake_measure, space_w=1.0, max_width=1000)
    assert len(lines) == 2


def test_wrap_greedy_wraps_on_width():
    lines = _wrap("aaaa bbbb cccc", _fake_measure, space_w=1.0, max_width=9)
    assert len(lines) >= 2


def test_wrap_skips_blank_paragraphs():
    lines = _wrap("a\n\nb", _fake_measure, space_w=1.0, max_width=1000)
    assert len(lines) == 2


def test_has_emoji_detects_real_examples():
    assert has_emoji("anniversary pics \U0001F979")  # 🥹
    assert has_emoji("too funny \U0001F602")  # 😂
    assert has_emoji("❤️ love it")  # ❤️ with variation selector


def test_has_emoji_false_for_plain_text():
    assert not has_emoji("just a normal header")
    assert not has_emoji("")
    assert not has_emoji("numbers 123 and symbols !?.")


def test_segment_splits_text_and_emoji_runs():
    runs = _segment("pics\U0001F979")
    assert runs == [("text", "pics"), ("emoji", "\U0001F979")]


def test_segment_pure_text():
    assert _segment("hello") == [("text", "hello")]


def test_plain_emoji_header_is_transparent_without_a_plate(monkeypatch, tmp_path):
    """Plain headers keep the color-emoji PNG path, but do not draw a plate."""
    if (
        header_image._first_existing(header_image._TEXT_FONT_CANDIDATES) is None
        or header_image._resolve_emoji_font() is None
    ):
        pytest.skip("Pillow-compatible text and color-emoji fonts are unavailable")

    def fail_if_plate(*_args, **_kwargs):
        raise AssertionError("plain header unexpectedly drew a plate")

    monkeypatch.setattr(
        header_image.ImageDraw.ImageDraw,
        "rounded_rectangle",
        fail_if_plate,
    )
    output = header_image.render_header_png(
        "hello 😂",
        tmp_path / "header.png",
        style_for_presets("classic", "plain"),
    )

    assert output.exists()
