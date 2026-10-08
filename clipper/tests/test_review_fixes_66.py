"""Cold-review repairs for RiceSuite #66 (PR #72).

Each test failed before its fix. Reviewer tags: C = correctness, A = ADR and
contract, S = skeptic.
"""

import re
from dataclasses import replace

import pytest
from PIL import Image

from app import main
from app.models import EmojiPick, HeaderPreviewRequest, RenderRequest, Word
from app.probe import MediaInfo
from render import emoji_track, framing, pipeline, text_image
from render.ass import (
    ACTIVE_SCALE,
    CAPTION_STYLE_NAMES,
    CAPTION_ZONE_PX,
    EMOJI_CAPTION_ZONE_PX,
    StyleConfig,
    build_ass,
    emoji_gap_below,
    layout_phrases,
    style_for_presets,
)
from render.pipeline import EMOJI_NOTE_PREFIX, render
from tests._util import words

WORDS = [
    Word(text=t, start=i * 0.4, end=i * 0.4 + 0.4)
    for i, t in enumerate(["pizza", "time", "with", "the", "crew"])
]


def _fake_row(clusters, height, gap):
    return Image.new("RGBA", (height * len(clusters), height), (255, 0, 0, 255))


@pytest.fixture
def ffmpeg(monkeypatch):
    class Completed:
        returncode = 0
        stderr = ""

    styles: list[StyleConfig] = []
    monkeypatch.setattr(pipeline, "run_owned", lambda cmd, **_k: Completed())
    monkeypatch.setattr(emoji_track, "draw_emoji_row", _fake_row)
    monkeypatch.setattr(text_image, "emoji_drawable", lambda cluster: True)
    monkeypatch.setattr(
        pipeline,
        "render_header_png",
        lambda _t, path, style, **_k: styles.append(style),
    )
    return styles


def _render(tmp_path, **kw):
    notes: list[str] = []
    req = RenderRequest(words=WORDS, header="A header", motion=False, **kw)
    info = MediaInfo(width=1080, height=1920, duration=3.0, has_audio=False)
    render(tmp_path, tmp_path / "source.mp4", info, req, notes=notes)
    return notes


# --- C1 / A1 / S6: the wider zone is only for clips that show emoji rows ------


def test_C1_a_clip_without_emoji_rows_keeps_the_540_px_zone(ffmpeg, tmp_path):
    assert CAPTION_ZONE_PX == 540
    _render(tmp_path)
    assert ffmpeg[0].caption_zone == CAPTION_ZONE_PX


def test_C1_a_clip_with_emoji_rows_keeps_the_header_above_them(ffmpeg, tmp_path):
    _render(tmp_path, emoji_on=True, emoji=[EmojiPick(word=0, emoji=["🍕"])])
    assert ffmpeg[0].caption_zone == EMOJI_CAPTION_ZONE_PX


def test_C1_picks_that_show_nowhere_do_not_widen_the_zone(ffmpeg, tmp_path):
    _render(tmp_path, emoji_on=False, emoji=[EmojiPick(word=0, emoji=["🍕"])])
    assert ffmpeg[0].caption_zone == CAPTION_ZONE_PX


def test_C1_a_failed_emoji_track_keeps_the_540_px_zone(ffmpeg, tmp_path, monkeypatch):
    def no_font(*_a, **_k):
        raise text_image.TextFontError("no colour-emoji font")

    monkeypatch.setattr(emoji_track, "draw_emoji_row", no_font)
    notes = _render(tmp_path, emoji_on=True, emoji=[EmojiPick(word=0, emoji=["🍕"])])
    assert ffmpeg[0].caption_zone == CAPTION_ZONE_PX
    assert notes == [f"{EMOJI_NOTE_PREFIX}no colour-emoji font"]


def test_C1_the_fallback_header_keeps_its_position_without_emoji_rows():
    style = replace(StyleConfig(), header_margin_v=1300)
    ass = build_ass([], duration=1.0, style=style, fallback_header="Low header")
    assert "\\pos(540,1300)" in ass
    wide = replace(style, caption_zone=EMOJI_CAPTION_ZONE_PX)
    ass = build_ass([], duration=1.0, style=wide, fallback_header="Low header")
    y = int(re.search(r"\\pos\(540,(\d+)\)", ass).group(1))
    assert y + 42 + 2 <= 1920 - EMOJI_CAPTION_ZONE_PX


def test_C1_the_preview_checks_the_zone_the_render_will_use(monkeypatch, tmp_path):
    from app import jobs

    monkeypatch.setattr(jobs, "WORK_ROOT", tmp_path)
    job = jobs.create_job()
    job.info = MediaInfo(1920, 1080, 2.0, False)
    seen = []
    monkeypatch.setattr(
        main.framing,
        "header_warning",
        lambda *a, **k: seen.append(k.get("caption_zone")),
    )
    monkeypatch.setattr(main, "header_png_bytes", lambda *a, **k: (b"", None))
    try:
        main.header_preview(job.id, HeaderPreviewRequest())
        main.header_preview(job.id, HeaderPreviewRequest(emoji_rows=True))
    finally:
        jobs._JOBS.pop(job.id, None)
    assert seen == [CAPTION_ZONE_PX] * 2 + [EMOJI_CAPTION_ZONE_PX] * 2


def test_C1_the_face_warning_zone_follows_the_clip():
    # A face ending at y=1250 of a 1920 output: clear of the 540 px zone,
    # inside the emoji zone.
    spans = [(1100.0, 1250.0)] * 5
    assert framing.zone_warning(spans, 1920, None) is None
    assert (
        framing.zone_warning(spans, 1920, None, caption_zone=EMOJI_CAPTION_ZONE_PX)
        == "caption_zone"
    )


# --- C3: a three-line phrase keeps its row inside the emoji zone -------------


@pytest.mark.parametrize("name", CAPTION_STYLE_NAMES)
@pytest.mark.parametrize("count", [2, 3])
def test_C3_every_row_stays_inside_the_emoji_zone(name, count):
    style = style_for_presets(name, "plain")
    # Words sized so the phrase breaks into ``count`` lines.
    px = {2: 0.5, 3: 0.9}[count] * style.font_size
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    (p,) = layout_phrases(ws, style, lambda t: len(t) * px, emoji={0: ("🔥",)})
    assert len(p.lines) == count
    assert p.row_box[0] >= style.play_res_y - EMOJI_CAPTION_ZONE_PX
    assert p.row_box[1] <= style.play_res_y - 300  # clear of the platform UI


def test_C3_a_top_line_anchor_of_a_three_line_phrase_goes_below_when_needed():
    style = StyleConfig()
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    (p,) = layout_phrases(ws, style, lambda t: len(t) * 86.0, emoji={0: ("🔥",)})
    assert len(p.lines) == 3
    assert p.row == "below"


# --- C2: rows on a millisecond clock, not image2's 1/25 s ------------------------


def test_C2_every_concat_entry_asks_for_a_millisecond_time_base():
    text = emoji_track.ffconcat_text([("a.png", 1_017_000), ("b.png", 523_000)])
    lines = text.splitlines()
    files = [i for i, line in enumerate(lines) if line.startswith("file ")]
    assert len(files) == 3
    for i in files:
        assert lines[i + 1] == f"option framerate {emoji_track.TRACK_FPS}"
    assert emoji_track.TRACK_FPS >= 1000


# --- S1: one emoji is one cluster, and one that cannot be drawn is left out ---


@pytest.mark.parametrize(
    "text",
    ["🍕", "👍🏽", "🇫🇷", "👨‍👩‍👧", "❤️", "☀️", "🏳️‍🌈", "🏖️"],
)
def test_S1_a_single_emoji_is_accepted(text):
    assert text_image.is_emoji_cluster(text)


@pytest.mark.parametrize(
    "text",
    [
        "🔥🔥",
        "🍕🍕🍕🍕🍕🍕",
        "🇫",
        "\ufe0f",
        "\u200d",
        "🍕\u200d",
        "\u200d🍕",
        "🏽",
        "🇫🇷🇩🇪",
    ],
)
def test_S1_runs_and_bare_modifiers_are_refused(text):
    assert not text_image.is_emoji_cluster(text)


def test_S1_an_emoji_the_font_cannot_draw_is_left_out_and_named(
    ffmpeg, tmp_path, monkeypatch
):
    monkeypatch.setattr(text_image, "emoji_drawable", lambda cluster: cluster != "★")
    notes = _render(
        tmp_path,
        emoji_on=True,
        emoji=[EmojiPick(word=0, emoji=["★"]), EmojiPick(word=4, emoji=["🎉", "★"])],
    )
    track = (tmp_path / emoji_track.LIST_NAME).read_text()
    assert track.count("file emoji/p") >= 1
    assert notes == [f"{EMOJI_NOTE_PREFIX}this computer cannot draw ★"]


# --- S4: a two-line phrase keeps both lines still as the highlight moves ------


def test_S4_lines_without_the_active_word_keep_its_height():
    style = replace(StyleConfig(), motion=True)
    ws = words(*[("aaaa", i * 0.4, i * 0.4 + 0.4) for i in range(5)])
    ass = build_ass(ws, duration=2.5, style=style, measure=lambda t: len(t) * 50.0)
    crisp = [
        d.split(",", 9)[9] for d in ass.splitlines() if d.startswith("Dialogue: 1")
    ]
    strut = f"\\alpha&HFF&\\fscx1\\fscy{ACTIVE_SCALE}"
    late = crisp[4]  # rests at 100%, the active word on the second line
    top, bottom = late.split("\\N")
    assert strut in top and strut not in bottom
    early = crisp[3 - 2]  # active word on the first line
    top, bottom = early.split("\\N")
    assert strut not in top and strut in bottom


# --- S5: a row below the text clears the descenders and the pop --------------


def test_S5_a_row_below_sits_further_from_the_text_than_one_above():
    style = StyleConfig()
    gap = emoji_gap_below(style)
    assert gap >= 18 + style.outline + round(0.1 * style.font_size)
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    (p,) = layout_phrases(ws, style, lambda t: len(t) * 50.0, emoji={4: ("🔥",)})
    assert p.row == "below"
    assert p.row_box[0] == p.bottom + gap
