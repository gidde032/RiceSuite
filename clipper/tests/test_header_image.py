import subprocess

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
    assert has_emoji("anniversary pics \U0001f979")  # 🥹
    assert has_emoji("too funny \U0001f602")  # 😂
    assert has_emoji("❤️ love it")  # ❤️ with variation selector


def test_has_emoji_false_for_plain_text():
    assert not has_emoji("just a normal header")
    assert not has_emoji("")
    assert not has_emoji("numbers 123 and symbols !?.")


def test_segment_splits_text_and_emoji_runs():
    runs = _segment("pics\U0001f979")
    assert runs == [("text", "pics"), ("emoji", "\U0001f979")]


def test_segment_pure_text():
    assert _segment("hello") == [("text", "hello")]


def test_plain_emoji_header_is_transparent_without_a_plate(monkeypatch, tmp_path):
    """Plain headers keep the color-emoji PNG path, but do not draw a plate."""
    if (
        header_image._resolve_text_font() is None
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


# --- Issue #3: font resolution off macOS -----------------------------------

DEBIAN_TEXT = "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
DEBIAN_EMOJI = "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"


@pytest.fixture
def fake_fonts(monkeypatch):
    """Pretend exactly the given font files exist, with fontconfig absent."""
    existing: set[str] = set()
    monkeypatch.setattr(header_image.os.path, "exists", lambda p: p in existing)
    monkeypatch.setattr(header_image.shutil, "which", lambda _name: None)
    header_image._resolve_text_font.cache_clear()
    header_image._resolve_emoji_font.cache_clear()
    yield existing
    header_image._resolve_text_font.cache_clear()
    header_image._resolve_emoji_font.cache_clear()


def _fake_fontconfig(monkeypatch, outputs):
    """Route fc-match/fc-list to canned stdout keyed by tool name."""
    monkeypatch.setattr(header_image.shutil, "which", lambda name: f"/bin/{name}")

    def run(cmd, **_kwargs):
        tool = cmd[0].rsplit("/", 1)[-1]
        out = outputs[tool]
        if isinstance(out, BaseException):
            raise out
        return subprocess.CompletedProcess(cmd, 0, stdout=out, stderr="")

    monkeypatch.setattr(header_image.subprocess, "run", run)


def test_linux_text_font_path_is_found(fake_fonts):
    fake_fonts.add(DEBIAN_TEXT)

    assert header_image._resolve_text_font() == DEBIAN_TEXT


def test_linux_noto_color_emoji_path_is_a_candidate(fake_fonts):
    fake_fonts.add(DEBIAN_EMOJI)

    assert header_image._emoji_font_paths() == [DEBIAN_EMOJI]


def test_macos_fonts_still_win_over_linux_paths(fake_fonts):
    mac_text = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
    mac_emoji = "/System/Library/Fonts/Apple Color Emoji.ttc"
    fake_fonts.update({DEBIAN_TEXT, DEBIAN_EMOJI, mac_text, mac_emoji})

    assert header_image._resolve_text_font() == mac_text
    assert header_image._emoji_font_paths()[0] == mac_emoji


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

    assert header_image._resolve_text_font() == text
    assert header_image._emoji_font_paths() == [DEBIAN_EMOJI, emoji]


def test_fontconfig_failure_is_treated_as_no_fonts(fake_fonts, monkeypatch):
    _fake_fontconfig(
        monkeypatch,
        {
            "fc-match": subprocess.TimeoutExpired("fc-match", 5),
            "fc-list": OSError("broken"),
        },
    )

    assert header_image._resolve_text_font() is None
    assert header_image._emoji_font_paths() == []


def test_missing_text_font_error_names_the_text_font(fake_fonts):
    with pytest.raises(header_image.HeaderFontError, match="text font"):
        header_image._require_header_fonts()


def test_missing_emoji_font_error_names_the_emoji_font(fake_fonts):
    fake_fonts.add(DEBIAN_TEXT)

    with pytest.raises(header_image.HeaderFontError, match="color-emoji font"):
        header_image._require_header_fonts()
