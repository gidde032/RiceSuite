"""Rendered-frame checks of caption Motion through real libass (RiceSuite #66).

These prove pixels, not strings: they render ASS from ``build_ass`` with
ffmpeg's ``subtitles`` filter, as the pipeline does, and measure the drawn
text. CI installs no ffmpeg by design (ci.yml), so they skip there and run
locally, like the #65 libass parity test.
"""

import shutil
import subprocess
from dataclasses import replace

import pytest
from PIL import Image, ImageChops

from render import text_image
from render.ass import CAPTION_STYLE_NAMES, POP_KEYFRAMES, build_ass, style_for_presets
from tests._util import words


def _has_libass() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
    ).stdout
    return " subtitles " in out


pytestmark = pytest.mark.skipif(
    not _has_libass(), reason="ffmpeg with libass is unavailable"
)

GREY = (128, 128, 128)


def _frames(tmp_path, ass: str, times: list[float], family: str) -> list[Image.Image]:
    """Render ``ass`` over grey and return the frames at ``times`` (seconds)."""
    (tmp_path / "c.ass").write_text(ass, encoding="utf-8")
    subtitles = "subtitles=c.ass"
    bundled = text_image.bundled_font_file(family)
    if bundled is not None:
        (tmp_path / "fonts").mkdir(exist_ok=True)
        shutil.copyfile(bundled, tmp_path / "fonts" / bundled.name)
        subtitles += ":fontsdir=fonts"
    frames = []
    for n, t in enumerate(times):
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=0x808080:s=1080x1920:r=100:d=4",
                "-vf",
                f"{subtitles},trim=start={t:.2f}",
                "-frames:v",
                "1",
                f"f{n}.png",
            ],
            check=True,
            cwd=tmp_path,
        )
        frames.append(Image.open(tmp_path / f"f{n}.png").convert("RGB"))
    return frames


def _ink(frame: Image.Image) -> tuple[int, int, int, int]:
    """Bounding box of the text and its crisp outline, not the soft shadow."""
    diff = ImageChops.difference(frame, Image.new("RGB", frame.size, GREY))
    box = diff.convert("L").point(lambda v: 255 if v > 100 else 0).getbbox()
    assert box is not None, "no caption was drawn"
    return box


def _ass(name: str, ws, motion: bool = True) -> tuple[str, str]:
    style = replace(style_for_presets(name, "plain"), motion=motion)
    measure = text_image.caption_measurer(
        style.font, style.bold, style.italic, style.font_size
    )
    return build_ass(ws, duration=3.0, style=style, measure=measure), style.font


def test_the_pop_scales_the_drawn_phrase_and_never_rewraps_it(tmp_path):
    ws = words(("so", 0.0, 1.0), ("here's", 1.0, 1.5), ("the", 1.5, 2.0))
    ws += words(("thing", 2.0, 2.5), ("about", 2.5, 3.0))
    ass, family = _ass("classic", ws)
    peak_s = POP_KEYFRAMES[1][0] / 1000
    start, peak, rest = (
        _ink(f) for f in _frames(tmp_path, ass, [0.0, peak_s, 0.5], family)
    )

    def size(box):
        return box[2] - box[0], box[3] - box[1]

    (w0, h0), (w1, h1), (w2, h2) = size(start), size(peak), size(rest)
    # Width and height follow the pop together: the lines did not re-wrap.
    assert w0 / w2 == pytest.approx(POP_KEYFRAMES[0][1] / 100, abs=0.05)
    assert h0 / h2 == pytest.approx(POP_KEYFRAMES[0][1] / 100, abs=0.05)
    assert w1 / w2 == pytest.approx(POP_KEYFRAMES[1][1] / 100, abs=0.05)
    assert h1 / h2 == pytest.approx(POP_KEYFRAMES[1][1] / 100, abs=0.05)


def test_the_bundled_font_is_really_drawn_not_a_fallback(tmp_path):
    ws = words(("MONTSERRAT", 0.0, 1.0))
    ass, family = _ass("montserrat", ws, motion=False)
    (frame,) = _frames(tmp_path, ass, [0.5], family)
    box = _ink(frame)
    style = style_for_presets("montserrat", "plain")
    own = text_image.caption_measurer(family, True, False, style.font_size)
    fallback = text_image.caption_measurer("Helvetica Neue", True, False, 100)
    drawn = box[2] - box[0] - 2 * style.outline
    assert drawn == pytest.approx(own("MONTSERRAT"), rel=0.03)
    assert abs(drawn - fallback("MONTSERRAT")) > 0.08 * drawn


@pytest.mark.parametrize("name", CAPTION_STYLE_NAMES)
def test_every_preset_stays_in_the_frame_at_the_peak_of_the_pop(tmp_path, name):
    ws = words(
        ("seriously", 0.0, 0.6),
        ("unbelievably", 0.6, 1.2),
        ("extraordinary", 1.2, 1.8),
        ("captions", 1.8, 2.4),
        ("everywhere", 2.4, 3.0),
    )
    ass, family = _ass(name, ws)
    peak_s = POP_KEYFRAMES[1][0] / 1000
    for frame in _frames(tmp_path, ass, [peak_s, 1.3], family):
        left, _top, right, _bottom = _ink(frame)
        assert left > 0
        assert right < 1080


def _colour_rows(frame: Image.Image) -> list[int]:
    """Rows of the frame holding saturated colour: emoji, not white/black/grey
    text, its outline, or the grey background."""
    hsv = frame.convert("HSV")
    sat = hsv.getchannel("S").point(lambda v: 255 if v > 120 else 0)
    val = hsv.getchannel("V").point(lambda v: 255 if v > 80 else 0)
    mask = ImageChops.multiply(sat, val)
    return [
        y
        for y in range(frame.height)
        if mask.crop((0, y, frame.width, y + 1)).getbbox()
    ]


@pytest.mark.parametrize(("anchor", "row"), [(0, "above"), (4, "below")])
def test_emoji_rows_are_drawn_in_colour_where_the_layout_puts_them(
    tmp_path, monkeypatch, anchor, row
):
    """Through the real pipeline: libass text plus the ffconcat overlay."""
    from app.models import EmojiPick, RenderRequest, Word
    from app.probe import MediaInfo
    from render import pipeline
    from render.ass import layout_phrases

    try:
        text_image.require_emoji_font()
    except text_image.TextFontError:
        pytest.skip("no colour-emoji font on this host")
    words_ = [
        Word(text=t, start=i * 0.4, end=i * 0.4 + 0.4)
        for i, t in enumerate(["everyone", "started", "dancing", "like", "crazy"])
    ]
    src = tmp_path / "src.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x808080:s=1080x1920:r=30:d=2",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
    )
    req = RenderRequest(
        words=words_,
        caption_style="classic",
        motion=False,
        emoji_on=True,
        emoji=[EmojiPick(word=anchor, emoji=["\U0001f483"])],
    )
    style = pipeline.style_for_request(req)
    measure = text_image.caption_measurer(
        style.font, style.bold, style.italic, style.font_size
    )
    (layout,) = layout_phrases(words_, style, measure, {anchor: ("\U0001f483",)})
    assert layout.row == row
    out = pipeline.render(
        tmp_path, src, MediaInfo(1080, 1920, 2.0, False), req, notes=(notes := [])
    )
    assert notes == []
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            "1.0",
            "-i",
            str(out),
            "-frames:v",
            "1",
            "f.png",
        ],
        check=True,
        cwd=tmp_path,
    )
    frame = Image.open(tmp_path / "f.png").convert("RGB")
    # The highlighted word is coloured too: leave out the text block's band.
    band = range(layout.top - 8, layout.bottom + 9)
    rows = [y for y in _colour_rows(frame) if y not in band]
    top, bottom = layout.row_box
    assert rows, "no colour emoji was drawn"
    assert top - 3 <= min(rows) and max(rows) <= bottom + 3
    assert max(rows) - min(rows) >= 0.8 * (bottom - top)
