"""The caption emoji track: one PNG per phrase row, played by ffconcat (#66).

libass on the macOS/CoreText toolchain draws colour emoji as empty boxes
(docs/spikes/emoji-burn-in.md), so caption emoji rows are drawn by Pillow, like
headers. Each phrase that shows a row gets a transparent full-frame PNG with
the row where ``render.ass.layout_phrases`` put it; a blank PNG fills the time
between rows. An ``ffconcat`` list gives each image its duration, and ffmpeg
reads the list as one input composited with one ``overlay``. With Motion, a
row starts with two pre-scaled pop frames.

Times are whole microseconds (ffmpeg's own time base) and every duration is
the difference of two absolute boundaries, so a long clip cannot drift and
each row starts exactly when its phrase does.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from render.ass import PhraseLayout, StyleConfig, emoji_row_height
from render.text_image import emoji_row

LIST_NAME = "emoji.ffconcat"
TRACK_DIR = "emoji"
BLANK_NAME = "blank.png"
# Space between two emoji in a row, as a fraction of the row height.
EMOJI_SPACING_EM = 0.12
# Motion: the pop frames' durations (ms) and scales (%), before the row rests
# at 100%. They follow the caption pop (70% -> 112% -> 100% over 160 ms).
POP_FRAMES = (40, 80)
POP_SCALES = (70, 112)

_US = 1_000_000

# The real row drawer; tests replace it, since CI has no colour-emoji font.
draw_emoji_row: Callable[[Sequence[str], int, int], Image.Image] = emoji_row


@dataclass(frozen=True)
class Segment:
    """A phrase's row on screen from ``start`` to ``end`` (seconds).

    ``frames`` are its image names: pop frames first, then the resting row.
    """

    start: float
    end: float
    frames: tuple[str, ...]


def timeline(
    segments: Sequence[Segment],
    duration: float,
    motion: bool,
    blank: str = BLANK_NAME,
) -> list[tuple[str, int]]:
    """(image, microseconds) entries covering ``0`` to ``duration`` exactly.

    A row ends at its phrase's end, at the next row's start, or at the clip's
    end, whichever comes first. Gaps show ``blank``. With ``motion``, a row's
    pop frames come first, cut short if the phrase is.
    """
    total = round(duration * _US)
    ordered = sorted(segments, key=lambda seg: seg.start)
    entries: list[tuple[str, int]] = []
    clock = 0
    for n, seg in enumerate(ordered):
        start = max(round(seg.start * _US), clock)
        end = min(round(seg.end * _US), total)
        if n + 1 < len(ordered):
            end = min(end, round(ordered[n + 1].start * _US))
        if end <= start:
            continue
        if start > clock:
            entries.append((blank, start - clock))
            clock = start
        pops = seg.frames[:-1] if motion else ()
        for name, ms in zip(pops, POP_FRAMES, strict=False):
            if clock >= end:
                break
            step = min(ms * 1000, end - clock)
            entries.append((name, step))
            clock += step
        if end > clock:
            entries.append((seg.frames[-1], end - clock))
            clock = end
    if total > clock:
        entries.append((blank, total - clock))
    return entries


def _seconds(us: int) -> str:
    return f"{us // _US}.{us % _US:06d}"


def ffconcat_text(entries: Sequence[tuple[str, int]]) -> str:
    """The ffconcat list. The demuxer applies a file's ``duration`` only when
    another file follows, so the last file is listed again."""
    lines = ["ffconcat version 1.0"]
    for name, us in entries:
        lines += [f"file {name}", f"duration {_seconds(us)}"]
    if entries:
        lines.append(f"file {entries[-1][0]}")
    return "\n".join(lines) + "\n"


def _place(
    canvas: tuple[int, int], row: Image.Image, height: int, centre_y: float
) -> Image.Image:
    """A transparent frame with ``row`` scaled to ``height``, centred on
    ``centre_y`` and on the frame's vertical midline."""
    width = max(1, round(row.width * height / row.height))
    scaled = row.resize((width, height), Image.LANCZOS)
    frame = Image.new("RGBA", canvas, (0, 0, 0, 0))
    frame.alpha_composite(
        scaled, ((canvas[0] - width) // 2, round(centre_y - height / 2))
    )
    return frame


def write_track(
    job_dir: Path,
    layouts: Sequence[PhraseLayout],
    style: StyleConfig,
    duration: float,
    draw_row: Callable[[Sequence[str], int, int], Image.Image] | None = None,
) -> str | None:
    """Draw every phrase's row and write the ffconcat list into ``job_dir``.

    Returns the list's file name, or None when no phrase shows a row. Raises
    when a row cannot be drawn (for example, no colour-emoji font); the
    caller then renders without emoji.
    """
    rows = [layout for layout in layouts if layout.row_box is not None]
    if not rows:
        return None
    draw = draw_row or draw_emoji_row
    track = Path(job_dir) / TRACK_DIR
    track.mkdir(exist_ok=True)
    canvas = (style.play_res_x, style.play_res_y)
    Image.new("RGBA", canvas, (0, 0, 0, 0)).save(track / BLANK_NAME)
    height = emoji_row_height(style)
    # Drawn once at the pop's largest size, then scaled down for each frame.
    big = round(height * max(POP_SCALES) / 100)
    segments = []
    for n, layout in enumerate(rows):
        row = draw(layout.emoji, big, round(big * EMOJI_SPACING_EM))
        top, bottom = layout.row_box
        centre = (top + bottom) / 2
        if style.motion:
            scales = (*POP_SCALES, 100)
            names = tuple(f"p{n}_{k}.png" for k in range(len(scales)))
        else:
            scales, names = (100,), (f"p{n}.png",)
        for name, pct in zip(names, scales, strict=True):
            size = height if pct == 100 else round(height * pct / 100)
            _place(canvas, row, size, centre).save(track / name)
        segments.append(
            Segment(layout.start, layout.end, tuple(f"{TRACK_DIR}/{x}" for x in names))
        )
    entries = timeline(
        segments, duration, style.motion, blank=f"{TRACK_DIR}/{BLANK_NAME}"
    )
    (Path(job_dir) / LIST_NAME).write_text(ffconcat_text(entries), encoding="utf-8")
    return LIST_NAME
