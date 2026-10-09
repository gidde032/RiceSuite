"""Caption motion, soft shadow, and the bundled-font preset (RiceSuite #66).

Motion is one per-clip toggle: the phrase pops in on its first event, the
highlighted word gets a small scale bump, and a blurred drop shadow sits under
crisp text. With Motion off the ASS is byte-identical to the output before
#66, which ``fixtures/ass_motion_off_golden.json`` holds (captured from
commit 268d54c before any #66 change).
"""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import ImageFont

from app.models import RenderRequest, Word
from app.probe import MediaInfo
from render import pipeline, text_image
from render.ass import (
    ACTIVE_SCALE,
    POP_KEYFRAMES,
    StyleConfig,
    build_ass,
    caption_lines,
    style_for_presets,
)
from render.pipeline import render, style_for_request
from tests._util import words

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = json.loads(
    (ROOT / "tests/fixtures/ass_motion_off_golden.json").read_text(encoding="utf-8")
)
GOLDEN_WORDS = words(*[tuple(w) for w in GOLDEN["words"]])


def _dialogues(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def _text(dialogue: str) -> str:
    return dialogue.split(",", 9)[9]


def _layer(dialogue: str) -> int:
    return int(dialogue.split(",", 1)[0].split(" ")[1])


def _start(dialogue: str) -> str:
    return dialogue.split(",")[1]


def _per_char(px: float = 50.0):
    """A fake measurer: every character (and space) is ``px`` wide."""
    return lambda text: len(text) * px


def _motion(style: StyleConfig | None = None) -> StyleConfig:
    return replace(style or StyleConfig(), motion=True)


# --- Motion off is today's output -------------------------------------------


# The pre-#66 presets that RiceSuite #79 kept unchanged. #79 cut the others and
# thickened Velvet Serif on purpose; the fixture still holds their old output.
UNCHANGED_SINCE_66 = ("punch", "friendly", "editorial", "lyric_block", "din_condensed")


@pytest.mark.parametrize("name", UNCHANGED_SINCE_66)
def test_motion_off_ass_is_byte_identical_to_before_66(name):
    style = style_for_presets(name, "plain")
    assert style.motion is False
    ass = build_ass(GOLDEN_WORDS, duration=8.0, style=style)
    assert ass == GOLDEN["cases"][f"preset:{name}"]


def test_motion_off_fallback_header_and_empty_scripts_are_unchanged():
    assert (
        build_ass(
            GOLDEN_WORDS,
            captions_on=False,
            duration=8.0,
            fallback_header="Big {news}\nline two",
            fallback_family="Arial",
        )
        == GOLDEN["cases"]["captions_off_fallback_plain"]
    )
    assert (
        build_ass(
            GOLDEN_WORDS,
            duration=8.0,
            style=style_for_presets("punch", "black_plate"),
            fallback_header="Plate header",
            fallback_family="Impact",
        )
        == GOLDEN["cases"]["fallback_plate"]
    )
    assert build_ass([], duration=3.0) == GOLDEN["cases"]["empty"]


def test_motion_off_render_writes_the_golden_ass(monkeypatch, tmp_path):
    _stub_ffmpeg(monkeypatch)
    req = RenderRequest(
        words=[Word(text=t, start=s, end=e) for t, s, e in GOLDEN["words"]],
        caption_style="punch",
        motion=False,
    )
    render(tmp_path, tmp_path / "source.mp4", _info(8.0), req)
    assert (tmp_path / "captions.ass").read_text(encoding="utf-8") == GOLDEN["cases"][
        "preset:punch"
    ]


# --- the phrase pop ----------------------------------------------------------


def _phrase_ws():
    return words(
        ("so", 0.0, 0.4),
        ("here's", 0.4, 0.8),
        ("the", 0.8, 1.2),
        ("thing", 1.2, 1.6),
    )


def test_pop_plays_only_on_the_first_event_of_each_phrase():
    ws = _phrase_ws() + words(("next", 3.0, 3.4), ("phrase", 3.4, 3.8))
    ass = build_ass(ws, duration=4.0, style=_motion(), measure=_per_char(20))
    events = [d for d in _dialogues(ass) if _layer(d) == 1]
    assert len(events) == 6
    first_scale = f"\\fscx{POP_KEYFRAMES[0][1]}"
    popping = [_start(d) for d in events if first_scale in _text(d)]
    # One pop per phrase, at the phrase start; later events never restart it.
    assert popping == ["0:00:00.00", "0:00:03.00"]
    for d in events:
        if _start(d) not in popping:
            assert "\\t(" not in _text(d).replace("\\t(0,60,", "")


def test_a_fast_first_word_continues_the_pop_instead_of_restarting_it():
    ws = words(("go", 0.0, 0.05), ("go", 0.05, 0.5), ("go", 0.5, 0.9))
    ass = build_ass(ws, duration=1.0, style=_motion(), measure=_per_char(20))
    second = [d for d in _dialogues(ass) if _layer(d) == 1][1]
    text = _text(second)
    # 50 ms in, the pop is part-way up and finishes on the original schedule.
    assert f"\\fscx{POP_KEYFRAMES[0][1]}\\" not in text
    peak_at, peak = POP_KEYFRAMES[1]
    assert f"\\t(0,{peak_at - 50},\\fscx{peak}\\fscy{peak})" in text
    assert f"\\t({peak_at - 50},{POP_KEYFRAMES[2][0] - 50}," in text


def test_no_reset_tag_drops_the_phrase_tags_after_the_active_word():
    ass = build_ass(_phrase_ws(), duration=2.0, style=_motion(), measure=_per_char(20))
    for d in _dialogues(ass):
        assert "\\r" not in _text(d)
    first = next(d for d in _dialogues(ass) if _layer(d) == 1)
    # The words after the active word keep the pop: their scale override
    # carries the same animation, and the colour is restored explicitly.
    after_active = _text(first).split("so", 1)[1]
    assert "\\c&HFFFFFF&" in after_active
    assert f"\\fscx{POP_KEYFRAMES[0][1]}" in after_active
    assert "\\t(" in after_active


def test_the_active_word_gets_the_scale_bump_and_the_rest_return_to_full_size():
    ass = build_ass(_phrase_ws(), duration=2.0, style=_motion(), measure=_per_char(20))
    third = [d for d in _dialogues(ass) if _layer(d) == 1][2]
    text = _text(third)
    before, after = text.split("the", 1)
    assert f"\\fscx{ACTIVE_SCALE}\\fscy{ACTIVE_SCALE}" in before.rsplit("{", 1)[1]
    assert after.startswith("{\\c&HFFFFFF&\\fscx100\\fscy100}")


def test_the_active_word_pops_with_the_phrase_on_the_first_event():
    ass = build_ass(_phrase_ws(), duration=2.0, style=_motion(), measure=_per_char(20))
    first = _text(next(d for d in _dialogues(ass) if _layer(d) == 1))
    active_tags = first.split("so", 1)[0].rsplit("{", 1)[1]
    start = round(POP_KEYFRAMES[0][1] * ACTIVE_SCALE / 100)
    end = round(POP_KEYFRAMES[-1][1] * ACTIVE_SCALE / 100)
    assert f"\\fscx{start}\\fscy{start}" in active_tags
    assert active_tags.rstrip("}").endswith(f"\\fscx{end}\\fscy{end})")


# --- the soft shadow ---------------------------------------------------------


def test_motion_draws_a_soft_shadow_layer_under_crisp_text():
    ass = build_ass(_phrase_ws(), duration=2.0, style=_motion(), measure=_per_char(20))
    shadow = [d for d in _dialogues(ass) if _layer(d) == 0]
    text = [d for d in _dialogues(ass) if _layer(d) == 1]
    assert len(shadow) == len(text) == 4
    for s, t in zip(shadow, text, strict=True):
        assert _start(s) == _start(t)
        # Only the blurred shadow of the shadow layer shows ...
        assert "\\1a&HFF&\\3a&HFF&" in _text(s)
        assert re.search(r"\\blur\d", _text(s))
        assert re.search(r"\\[xy]shad\d", _text(s))
        # ... and the text layer has a crisp outline and no shadow of its own.
        assert "\\shad0" in _text(t)
        assert "\\blur" not in _text(t)
        # The shadow carries no highlight colour, only the same scale motion.
        assert "\\c&H" not in _text(s)


# --- explicit line breaks ----------------------------------------------------


def test_motion_breaks_lines_itself_so_the_pop_cannot_rewrap_them():
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    ass = build_ass(ws, duration=2.0, style=_motion(), measure=_per_char(50))
    # Per event (\q2), so a libass fallback header still wraps itself.
    assert "WrapStyle: 0" in ass
    for d in _dialogues(ass):
        assert "\\q2" in _text(d)
        # Drop the hidden line-height strut, then every tag.
        plain = re.sub(r"\{\\alpha&HFF&[^}]*\}x", "", _text(d))
        plain = re.sub(r"\{[^}]*\}", "", plain)
        assert plain == "aaaa aaaa aaaa\\Naaaa aaaa"


def test_caption_lines_balance_like_libass_and_keep_room_for_the_bump():
    style = StyleConfig()
    lines, scale = caption_lines(["aaaa"] * 5, style, _per_char(50))
    assert lines == [[0, 1, 2], [3, 4]]
    assert scale == 1.0
    # One line when it fits with the bump and outline.
    lines, scale = caption_lines(["aa"] * 5, style, _per_char(50))
    assert lines == [[0, 1, 2, 3, 4]]


def test_a_word_wider_than_the_frame_shrinks_the_phrase_to_fit():
    style = StyleConfig()
    lines, scale = caption_lines(["a" * 30], style, _per_char(50))
    assert lines == [[0]]
    assert 0.5 < scale < 0.65
    ws = words(("a" * 30, 0.0, 1.0))
    ass = build_ass(ws, duration=1.0, style=_motion(), measure=_per_char(50))
    pct = round(scale * 100)
    assert f"\\fscx{pct}\\fscy{pct}" in _text(_dialogues(ass)[-1])


def test_every_event_of_a_phrase_shares_one_fixed_position():
    ass = build_ass(_phrase_ws(), duration=2.0, style=_motion(), measure=_per_char(20))
    positions = {
        re.search(r"\\pos\([^)]*\)", _text(d)).group(0)
        for d in _dialogues(ass)
        if _layer(d) == 1
    }
    assert len(positions) == 1


def test_motion_keeps_braces_and_backslashes_escaped():
    ws = words(("{x}", 0.0, 0.3), ("a\\b", 0.3, 0.6))
    ass = build_ass(ws, duration=1.0, style=_motion(), measure=_per_char(20))
    for d in _dialogues(ass):
        assert "\\{x\\}" in _text(d)
        assert "a\\\\b" in _text(d)


def test_motion_without_a_measurer_still_breaks_lines():
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    ass = build_ass(ws, duration=2.0, style=_motion())
    assert "\\q2" in _dialogues(ass)[0]
    assert "\\N" in _dialogues(ass)[0]


# --- the request -------------------------------------------------------------


def test_motion_is_on_by_default_and_carried_into_the_style():
    assert RenderRequest().motion is True
    assert style_for_request(RenderRequest()).motion is True
    assert style_for_request(RenderRequest(motion=False)).motion is False


def test_motion_render_measures_with_the_caption_font(monkeypatch, tmp_path):
    _stub_ffmpeg(monkeypatch)
    req = RenderRequest(words=[Word(text="hello", start=0.0, end=0.5)])
    render(tmp_path, tmp_path / "source.mp4", _info(1.0), req)
    ass = (tmp_path / "captions.ass").read_text(encoding="utf-8")
    assert "\\q2" in ass
    assert "\\blur" in ass


# --- the bundled Montserrat preset ------------------------------------------

FONT_FILE = text_image.BUNDLED_FONTS_DIR / "Montserrat-Black.ttf"


def test_montserrat_and_its_licence_are_committed():
    assert FONT_FILE.is_file()
    licence = (text_image.BUNDLED_FONTS_DIR / "OFL.txt").read_text(encoding="utf-8")
    assert "SIL Open Font License, Version 1.1" in licence
    assert "Montserrat" in licence


def test_the_preset_names_the_bundled_face_exactly():
    # libass matches the ASS Fontname against the face's full name; the bare
    # family "Montserrat" silently falls back to Helvetica (checked 2026-10-06).
    style = style_for_presets("montserrat", "plain")
    family, face = ImageFont.truetype(str(FONT_FILE), 20).getname()
    assert style.font == f"{family} {face}" == "Montserrat Black"
    assert text_image.bundled_font_file(style.font) == FONT_FILE


def test_the_caption_font_resolves_to_the_bundled_file_for_pillow():
    ref = text_image.caption_font("Montserrat Black", bold=True, italic=False)
    assert ref is not None
    assert Path(ref.path) == FONT_FILE


def test_captions_are_measured_at_the_size_libass_draws_them():
    # libass sizes a face by its OS/2 win ascent + descent; Montserrat's hhea
    # metrics are 28% smaller, which broke lines far too early (#66 tuning:
    # libass draws "so here's the thing about" 872 px wide at size 100).
    ref = text_image.FontRef(str(FONT_FILE))
    assert text_image._win_height(ref) == (1000, 1562)
    measure = text_image.caption_measurer("Montserrat Black", True, False, 100)
    assert measure("so here's the thing about") == pytest.approx(872, rel=0.02)
    style = style_for_presets("montserrat", "plain")
    lines, _scale = caption_lines("so here's the thing about".split(), style, measure)
    assert len(lines) == 1


def test_a_face_without_os2_metrics_falls_back_to_the_pillow_scale(tmp_path):
    missing = text_image.FontRef(str(tmp_path / "none.ttf"))
    assert text_image._win_height(missing) is None


def test_render_passes_the_bundled_font_through_fontsdir(monkeypatch, tmp_path):
    seen = _stub_ffmpeg(monkeypatch)
    req = RenderRequest(
        words=[Word(text="hello", start=0.0, end=0.5)], caption_style="montserrat"
    )
    render(tmp_path, tmp_path / "source.mp4", _info(1.0), req)
    graph = _filter_graph(seen[0])
    assert "subtitles=captions.ass:fontsdir=fonts" in graph
    copied = tmp_path / "fonts" / FONT_FILE.name
    assert copied.read_bytes() == FONT_FILE.read_bytes()
    ass = (tmp_path / "captions.ass").read_text(encoding="utf-8")
    assert "Style: Caption,Montserrat Black," in ass


def test_a_system_font_preset_adds_no_fontsdir(monkeypatch, tmp_path):
    seen = _stub_ffmpeg(monkeypatch)
    req = RenderRequest(
        words=[Word(text="hello", start=0.0, end=0.5)], caption_style="punch"
    )
    render(tmp_path, tmp_path / "source.mp4", _info(1.0), req)
    assert "fontsdir" not in _filter_graph(seen[0])
    assert not (tmp_path / "fonts").exists()


# --- helpers -----------------------------------------------------------------


def _info(duration: float) -> MediaInfo:
    return MediaInfo(width=1080, height=1920, duration=duration, has_audio=False)


def _stub_ffmpeg(monkeypatch) -> list[list[str]]:
    seen: list[list[str]] = []

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        return Completed()

    monkeypatch.setattr(pipeline, "run_owned", fake_run)
    return seen


def _filter_graph(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]
