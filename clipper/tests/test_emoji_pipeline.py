"""Emoji rows in the render: inputs, one overlay, and soft failure (#66)."""

import itertools
import re

import pytest
from PIL import Image

from app.models import EmojiPick, MusicSettings, RenderRequest, Word
from app.probe import MediaInfo
from render import emoji_track, pipeline, text_image
from render.ass import emoji_lift, layout_phrases, style_for_presets
from render.pipeline import render

WORDS = [
    Word(text=t, start=i * 0.4, end=i * 0.4 + 0.4)
    for i, t in enumerate(
        ["pizza", "time", "with", "the", "crew", "then", "we", "went", "home"]
    )
]


def _fake_row(clusters, height, gap):
    return Image.new("RGBA", (height * len(clusters), height), (255, 0, 0, 255))


@pytest.fixture
def ffmpeg(monkeypatch):
    seen: list[list[str]] = []

    class Completed:
        returncode = 0
        stderr = ""

    def fake_run(cmd, **_kwargs):
        seen.append(cmd)
        return Completed()

    monkeypatch.setattr(pipeline, "run_owned", fake_run)
    monkeypatch.setattr(emoji_track, "draw_emoji_row", _fake_row)
    monkeypatch.setattr(
        pipeline,
        "render_header_png",
        lambda _t, path, _s, **_k: Image.new("RGBA", (1080, 1920)).save(path),
    )
    return seen


def _inputs(cmd: list[str]) -> list[str]:
    return [cmd[i + 1] for i, arg in enumerate(cmd) if arg == "-i"]


def _graph(cmd: list[str]) -> str:
    return cmd[cmd.index("-filter_complex") + 1]


def _req(music=False, header=False, emoji=True, **kw):
    return RenderRequest(
        words=WORDS,
        header="A header" if header else "",
        music=MusicSettings(mode="mix", filename="music.m4a")
        if music
        else MusicSettings(),
        emoji_on=emoji,
        emoji=[EmojiPick(word=0, emoji=["🍕"])],
        **kw,
    )


@pytest.mark.parametrize(
    ("music", "header", "emoji"), list(itertools.product([False, True], repeat=3))
)
def test_every_input_combination_overlays_the_right_streams(
    ffmpeg, tmp_path, music, header, emoji
):
    if music:
        (tmp_path / "music.m4a").write_bytes(b"x")
    info = MediaInfo(width=1080, height=1920, duration=4.0, has_audio=True)
    render(tmp_path, tmp_path / "source.mp4", info, _req(music, header, emoji))
    cmd = ffmpeg[-1]
    inputs = _inputs(cmd)
    graph = _graph(cmd)
    assert inputs[0].endswith("source.mp4")
    if music:
        assert inputs[1].endswith("music.m4a")
        assert "[1:a]" in graph
    overlays = re.findall(r"\[(\d+):v\]overlay=0:0(:eof_action=pass)?", graph)
    expected = []
    if emoji:
        e = inputs.index(emoji_track.LIST_NAME)
        # The concat demuxer reads the list as one input.
        k = cmd.index(emoji_track.LIST_NAME)
        assert cmd[k - 5 : k] == ["-f", "concat", "-safe", "0", "-i"]
        expected.append((str(e), ":eof_action=pass"))
    if header:
        expected.append((str(inputs.index(pipeline.HEADER_PNG)), ""))
    assert overlays == expected
    assert graph.count("[vout]") == 1
    # Each labelled pad is produced once and consumed once.
    for label in set(re.findall(r"\[([a-z]\w*)\]", graph)) - {"vout", "aout"}:
        assert graph.count(f"[{label}]") == 2, label


def test_emoji_off_sends_no_track_and_no_lift(ffmpeg, tmp_path):
    info = MediaInfo(width=1080, height=1920, duration=4.0, has_audio=False)
    render(tmp_path, tmp_path / "source.mp4", info, _req(emoji=False, motion=False))
    assert emoji_track.LIST_NAME not in _inputs(ffmpeg[-1])
    assert "\\pos(" not in (tmp_path / "captions.ass").read_text()


def test_emoji_rows_lift_the_captions(ffmpeg, tmp_path):
    info = MediaInfo(width=1080, height=1920, duration=4.0, has_audio=False)
    render(tmp_path, tmp_path / "source.mp4", info, _req(motion=False))
    ass = (tmp_path / "captions.ass").read_text()
    style = style_for_presets("classic", "plain")
    measure = text_image.caption_measurer(
        style.font, style.bold, style.italic, style.font_size
    )
    flat = layout_phrases(WORDS, style, measure)
    ys = [int(y) for y in re.findall(r"\\pos\(540,(\d+)\)", ass)]
    # Every phrase rises by one row, the one with emoji and the one without.
    first, second = flat[0].center_y, flat[1].center_y
    lift = emoji_lift(style)
    assert set(ys) == {first - lift, second - lift}


def test_a_missing_emoji_font_drops_the_rows_but_not_the_render(
    ffmpeg, tmp_path, monkeypatch
):
    def no_font(*_a, **_k):
        raise text_image.TextFontError("missing a renderable color-emoji font")

    monkeypatch.setattr(emoji_track, "draw_emoji_row", no_font)
    notes: list[str] = []
    info = MediaInfo(width=1080, height=1920, duration=4.0, has_audio=False)
    render(tmp_path, tmp_path / "source.mp4", info, _req(motion=False), notes=notes)
    assert emoji_track.LIST_NAME not in _inputs(ffmpeg[-1])
    assert "\\pos(" not in (tmp_path / "captions.ass").read_text()
    assert notes == [
        "The caption emoji were left out: missing a renderable color-emoji font"
    ]


def test_captions_off_draws_no_emoji(ffmpeg, tmp_path):
    info = MediaInfo(width=1080, height=1920, duration=4.0, has_audio=False)
    render(tmp_path, tmp_path / "source.mp4", info, _req(captions_on=False))
    assert emoji_track.LIST_NAME not in _inputs(ffmpeg[-1])


def test_render_request_validates_emoji_picks():
    with pytest.raises(ValueError):
        EmojiPick(word=0, emoji=["🍕", "🍺", "🎉"])
    with pytest.raises(ValueError):
        EmojiPick(word=0, emoji=["pizza"])
    with pytest.raises(ValueError):
        EmojiPick(word=-1, emoji=["🍕"])
    with pytest.raises(ValueError):
        EmojiPick(word=0, emoji=[])
    assert EmojiPick(word=2, emoji=["👍🏽", "🇫🇷"]).emoji == ["👍🏽", "🇫🇷"]
    assert RenderRequest().emoji_on is False
    assert RenderRequest().emoji == []
