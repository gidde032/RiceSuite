import subprocess

import pytest

from render import text_image
from render.ass import style_for_presets
from render.header_image import header_layer
from render.text_image import has_emoji, segment, wrap


def _fake_measure(word):
    # width == character count; parts unused by _wrap
    return (word, float(len(word)))


def test_wrap_honors_explicit_newlines_as_hard_breaks():
    # Regression: the emoji path split on " " only, mangling 2-line headers.
    lines = wrap("line one\nline two", _fake_measure, space_w=1.0, max_width=1000)
    assert len(lines) == 2


def test_wrap_greedy_wraps_on_width():
    lines = wrap("aaaa bbbb cccc", _fake_measure, space_w=1.0, max_width=9)
    assert len(lines) >= 2


def _words(lines):
    return [" ".join(word for word, _w in parts) for parts, _width in lines]


def test_wrap_balances_soft_breaks_like_libass():
    # Greedy gives "aaaa bbbb cccc" / "dd"; libass WrapStyle 0 evens the
    # lines out, upper line wider.
    lines = wrap("aaaa bbbb cccc dd", _fake_measure, space_w=1.0, max_width=14)
    assert _words(lines) == ["aaaa bbbb", "cccc dd"]


def test_wrap_does_not_balance_across_hard_breaks():
    lines = wrap("aaaa bbbb cccc\ndd", _fake_measure, space_w=1.0, max_width=100)
    assert _words(lines) == ["aaaa bbbb cccc", "dd"]


def test_wrap_keeps_an_over_long_word_on_its_own_line():
    lines = wrap("a " + "x" * 30 + " b", _fake_measure, space_w=1.0, max_width=10)
    assert _words(lines) == ["a", "x" * 30, "b"]


def test_wrap_keeps_blank_paragraphs_as_blank_lines():
    # libass draws "a\N\Nb" with a blank line between (RiceSuite #65 review).
    lines = wrap("a\n\nb", _fake_measure, space_w=1.0, max_width=1000)
    assert [parts for parts, _w in lines][1] == []
    assert len(lines) == 3


def test_has_emoji_detects_real_examples():
    assert has_emoji("anniversary pics \U0001f979")  # 🥹
    assert has_emoji("too funny \U0001f602")  # 😂
    assert has_emoji("❤️ love it")  # ❤️ with variation selector


def test_has_emoji_false_for_plain_text():
    assert not has_emoji("just a normal header")
    assert not has_emoji("")
    assert not has_emoji("numbers 123 and symbols !?.")


def test_segment_splits_text_and_emoji_runs():
    runs = segment("pics\U0001f979")
    assert runs == [("text", "pics"), ("emoji", "\U0001f979")]


def test_segment_pure_text():
    assert segment("hello") == [("text", "hello")]


def test_plain_emoji_header_is_transparent_without_a_plate():
    """A plain header draws no plate: outside the glyphs it stays transparent."""
    text_image.clear_font_caches()
    if (
        text_image._resolve_text_font() is None
        or text_image._resolve_emoji_font() is None
    ):
        pytest.skip("Pillow-compatible text and color-emoji fonts are unavailable")

    layer = header_layer("hello 😂", style_for_presets("classic", "plain"))
    box = layer.box
    # Just left of the first glyph, inside the line box: nothing drawn.
    assert layer.image.getpixel((box.left - 3, (box.top + box.bottom) // 2))[3] == 0


# --- Issue #3: font resolution off macOS -----------------------------------

DEBIAN_TEXT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
DEBIAN_EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"


@pytest.fixture
def fake_fonts(monkeypatch):
    """Pretend exactly the given font files exist, with fontconfig absent."""
    existing: set[str] = set()
    monkeypatch.setattr(text_image.os.path, "exists", lambda p: p in existing)
    monkeypatch.setattr(text_image.shutil, "which", lambda _name: None)
    text_image.clear_font_caches()
    yield existing
    text_image.clear_font_caches()


def _fake_fontconfig(monkeypatch, outputs):
    """Route fc-match/fc-list to canned stdout keyed by tool name."""
    monkeypatch.setattr(text_image.shutil, "which", lambda name: f"/bin/{name}")

    def run(cmd, **_kwargs):
        tool = cmd[0].rsplit("/", 1)[-1]
        out = outputs[tool]
        if isinstance(out, BaseException):
            raise out
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(text_image.subprocess, "run", run)


def test_linux_text_font_path_is_found(fake_fonts):
    fake_fonts.add(DEBIAN_TEXT)

    assert text_image._resolve_text_font() == DEBIAN_TEXT


def test_linux_noto_color_emoji_path_is_a_candidate(fake_fonts):
    fake_fonts.add(DEBIAN_EMOJI)

    assert text_image._emoji_font_paths() == [DEBIAN_EMOJI]


def test_macos_fonts_still_win_over_linux_paths(fake_fonts):
    mac_text = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    mac_emoji = "/System/Library/Fonts/Apple Color Emoji.ttc"
    fake_fonts.update({DEBIAN_TEXT, DEBIAN_EMOJI, mac_text, mac_emoji})

    assert text_image._resolve_text_font() == mac_text
    assert text_image._emoji_font_paths()[0] == mac_emoji


def test_fontconfig_fallback_finds_fonts_outside_known_paths(fake_fonts, monkeypatch):
    text = "/opt/fonts/Sans-Bold.ttf"
    emoji = "/opt/fonts/Emoji.ttf"
    fake_fonts.update({text, emoji, DEBIAN_EMOJI})
    _fake_fontconfig(
        monkeypatch,
        {
            "fc-match": f"{text}\n",
            # Duplicates, a known candidate, and a non-loadable bitmap font.
            "fc-list": f"{emoji}\n{emoji}\n{DEBIAN_EMOJI}\n/opt/fonts/x.pcf.gz\n",
        },
    )

    assert text_image._resolve_text_font() == text
    assert text_image._emoji_font_paths() == [DEBIAN_EMOJI, emoji]


def test_fontconfig_failure_is_treated_as_no_fonts(fake_fonts, monkeypatch):
    _fake_fontconfig(
        monkeypatch,
        {
            "fc-match": subprocess.TimeoutExpired("fc-match", 5),
            "fc-list": OSError("broken"),
        },
    )

    assert text_image._resolve_text_font() is None
    assert text_image._emoji_font_paths() == []


def test_missing_text_font_error_names_the_text_font(fake_fonts):
    with pytest.raises(text_image.TextFontError, match="text font"):
        text_image.resolve_font("arial")


def test_missing_emoji_font_error_names_the_emoji_font(fake_fonts):
    fake_fonts.add(DEBIAN_TEXT)

    with pytest.raises(text_image.TextFontError, match="color-emoji font"):
        text_image.require_emoji_font()


# --- RiceSuite #65: curated fonts -------------------------------------------


def test_missing_curated_font_falls_back_to_the_default(fake_fonts):
    fake_fonts.add(DEBIAN_TEXT)

    assert text_image.resolve_font("impact") == text_image.FontRef(DEBIAN_TEXT)
    assert text_image.font_available("impact") is False
    assert text_image.font_available("arial") is True


def test_curated_font_resolves_its_own_face(fake_fonts, monkeypatch):
    impact = "/System/Library/Fonts/Supplemental/Impact.ttf"
    fake_fonts.update({DEBIAN_TEXT, impact})

    assert text_image.resolve_font("impact") == text_image.FontRef(impact, 0)
    assert text_image.font_available("impact") is True


def test_named_face_is_found_inside_a_collection(fake_fonts, monkeypatch):
    ttc = "/System/Library/Fonts/HelveticaNeue.ttc"
    fake_fonts.add(ttc)
    names = ["Regular", "Bold"]

    class Face:
        def __init__(self, index):
            if index >= len(names):
                raise OSError("no such face")
            self.index = index

        def getname(self):
            return ("Helvetica Neue", names[self.index])

    monkeypatch.setattr(
        text_image.ImageFont, "truetype", lambda _p, _s, index=0: Face(index)
    )
    assert text_image.resolve_font("helvetica") == text_image.FontRef(ttc, 1)
    names[1] = "Light"
    text_image.clear_font_caches()
    assert text_image._resolve_choice("helvetica") is None


def test_request_font_list_matches_the_curated_fonts():
    from typing import get_args

    from app.models import HeaderFont

    assert set(get_args(HeaderFont)) == set(text_image.FONT_CHOICES)
    assert text_image.DEFAULT_FONT == "arial"


def test_ass_font_sizes_the_line_box_like_libass():
    text_image.clear_font_caches()
    path = text_image._resolve_text_font()
    if path is None:
        pytest.skip("no Pillow-compatible text font on this host")
    font = text_image.ass_font(text_image.FontRef(path), 42)
    ascent, descent = font.getmetrics()
    assert abs((ascent + descent) - 42) <= 1
