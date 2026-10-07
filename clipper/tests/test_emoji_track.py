"""The caption emoji track: per-phrase PNGs and one ffconcat list (#66).

Each phrase with emoji gets a transparent full-frame PNG (plus pop frames
with Motion); a blank PNG fills the gaps. ffmpeg reads the list as one input
and composites it with a single overlay. The concat demuxer applies a file's
``duration`` only when another entry follows, so the last file is repeated.
"""

from dataclasses import replace

import pytest
from PIL import Image

from render import emoji_track
from render.ass import StyleConfig, emoji_row_height, layout_phrases
from render.emoji_track import (
    BLANK_NAME,
    LIST_NAME,
    POP_FRAMES,
    Segment,
    ffconcat_text,
    timeline,
    write_track,
)
from tests._util import words


def _fake_row(clusters, height, gap):
    """A solid stand-in for the emoji row: one square per cluster."""
    width = height * len(clusters) + gap * (len(clusters) - 1)
    return Image.new("RGBA", (width, height), (255, 0, 0, 255))


def _durations(entries):
    return [us for _name, us in entries]


# --- the timeline ------------------------------------------------------------


def test_gaps_are_blank_and_the_track_ends_at_the_clip_end():
    segments = [Segment(1.0, 2.0, ("p0.png",)), Segment(3.5, 4.0, ("p1.png",))]
    entries = timeline(segments, duration=6.0, motion=False)
    assert entries == [
        (BLANK_NAME, 1_000_000),
        ("p0.png", 1_000_000),
        (BLANK_NAME, 1_500_000),
        ("p1.png", 500_000),
        (BLANK_NAME, 2_000_000),
    ]


def test_the_last_file_is_repeated_so_its_duration_applies():
    entries = [(BLANK_NAME, 1_000_000), ("p0.png", 2_000_000)]
    text = ffconcat_text(entries)
    lines = text.strip().splitlines()
    assert lines[0] == "ffconcat version 1.0"
    assert lines[1:] == [
        f"file {BLANK_NAME}",
        "duration 1.000000",
        "file p0.png",
        "duration 2.000000",
        "file p0.png",
    ]


def test_a_phrase_at_zero_and_one_to_the_end_need_no_blank():
    entries = timeline([Segment(0.0, 3.0, ("p0.png",))], duration=3.0, motion=False)
    assert entries == [("p0.png", 3_000_000)]


def test_a_long_clip_does_not_drift():
    # 400 phrases over ~10 minutes at awkward times: each phrase's image must
    # start exactly at its phrase start, and the track must end at the clip
    # end, with no rounding error accumulating.
    segments = []
    t = 0.137
    for i in range(400):
        start, end = t, t + 0.8 + (i % 7) * 0.113
        segments.append(Segment(start, end, (f"p{i}.png",)))
        t = end + 0.0371 * (i % 3)
    duration = t + 1.234567
    entries = timeline(segments, duration=duration, motion=False)
    clock = 0
    starts = {}
    for name, us in entries:
        assert us > 0
        starts.setdefault(name, clock)
        clock += us
    assert clock == round(duration * 1_000_000)
    for i, seg in enumerate(segments):
        assert starts[f"p{i}.png"] == round(seg.start * 1_000_000)


def test_motion_starts_each_row_with_its_pop_frames():
    frames = ("p0_0.png", "p0_1.png", "p0_2.png")
    entries = timeline([Segment(1.0, 2.0, frames)], duration=2.0, motion=True)
    pop = [ms * 1000 for ms in POP_FRAMES]
    assert entries == [
        (BLANK_NAME, 1_000_000),
        ("p0_0.png", pop[0]),
        ("p0_1.png", pop[1]),
        ("p0_2.png", 1_000_000 - pop[0] - pop[1]),
    ]


def test_a_phrase_shorter_than_the_pop_cuts_it_short():
    frames = ("p0_0.png", "p0_1.png", "p0_2.png")
    entries = timeline([Segment(1.0, 1.05, frames)], duration=1.05, motion=True)
    assert entries[1:] == [("p0_0.png", POP_FRAMES[0] * 1000), ("p0_1.png", 10_000)]
    assert sum(_durations(entries)) == 1_050_000


def test_overlapping_phrases_never_go_back_in_time():
    segments = [Segment(0.0, 1.5, ("a.png",)), Segment(1.2, 2.0, ("b.png",))]
    entries = timeline(segments, duration=2.0, motion=False)
    assert entries == [("a.png", 1_200_000), ("b.png", 800_000)]


def test_rows_past_the_clip_end_are_trimmed():
    segments = [Segment(1.0, 9.0, ("a.png",)), Segment(9.5, 10.0, ("b.png",))]
    entries = timeline(segments, duration=3.0, motion=False)
    assert entries == [(BLANK_NAME, 1_000_000), ("a.png", 2_000_000)]


# --- the PNGs ----------------------------------------------------------------


def _two_line_layout(anchor, style=None):
    ws = words(*[("aaaa", i * 0.3, i * 0.3 + 0.3) for i in range(5)])
    return layout_phrases(
        ws, style or StyleConfig(), lambda t: len(t) * 50.0, emoji={anchor: ("🔥",)}
    )


@pytest.mark.parametrize("anchor", [0, 4])
def test_each_row_png_is_drawn_where_the_layout_puts_it(tmp_path, anchor):
    style = StyleConfig()
    layout = _two_line_layout(anchor, style)
    name = write_track(tmp_path, layout, style, duration=2.0, draw_row=_fake_row)
    assert name == LIST_NAME
    (phrase,) = layout
    png = Image.open(tmp_path / "emoji" / "p0.png")
    assert png.size == (style.play_res_x, style.play_res_y)
    left, top, right, bottom = png.getchannel("A").getbbox()
    assert (top, bottom) == phrase.row_box
    height = emoji_row_height(style)
    assert right - left == height
    assert (left + right) // 2 == style.play_res_x // 2
    blank = Image.open(tmp_path / "emoji" / BLANK_NAME)
    assert blank.getchannel("A").getbbox() is None


def test_motion_writes_scaled_pop_frames_centred_on_the_row(tmp_path):
    style = replace(StyleConfig(), motion=True)
    layout = _two_line_layout(0, style)
    write_track(tmp_path, layout, style, duration=2.0, draw_row=_fake_row)
    boxes = [
        Image.open(tmp_path / "emoji" / f"p0_{k}.png").getchannel("A").getbbox()
        for k in range(len(POP_FRAMES) + 1)
    ]
    sizes = [b[3] - b[1] for b in boxes]
    rest = sizes[-1]
    assert sizes[0] < rest < sizes[1]
    centre = [(b[1] + b[3]) / 2 for b in boxes]
    assert max(centre) - min(centre) <= 1
    text = (tmp_path / LIST_NAME).read_text()
    assert "file emoji/p0_0.png" in text


def test_no_picks_writes_no_track(tmp_path):
    ws = words(("hi", 0.0, 0.5))
    layout = layout_phrases(ws, StyleConfig(), lambda t: 10.0 * len(t), emoji={})
    assert write_track(tmp_path, layout, StyleConfig(), 1.0, draw_row=_fake_row) is None
    assert not (tmp_path / LIST_NAME).exists()


def test_the_real_row_drawer_is_the_shared_emoji_row():
    assert emoji_track.draw_emoji_row.__name__ == "emoji_row"
