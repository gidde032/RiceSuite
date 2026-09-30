"""Issue #20: the header sits ~11% from the top, the same on both render paths.

Text-only headers are burned by libass from the ASS ``Header`` style; emoji
headers are drawn by ``render.header_image`` and overlaid. Both read
``StyleConfig.header_margin_v``, and the face-warning header zone in
``render.framing`` is derived from it.
"""

import re
import shutil
import subprocess
from dataclasses import replace

import pytest
from PIL import Image, ImageChops

from app.models import TrackSample
from render import framing, header_image
from render.ass import HEADER_STYLE_NAMES, StyleConfig, build_ass, style_for_presets
from render.framing import HEADER_ZONE_PX, plan_crop

HEADER_TOP_PX = 210  # ~11% of 1920; Issue #20 (was 450, ~23%)
TWO_LINE = "Two lines of header text\nwith a second line under it"
_HEADER_STYLE_RE = re.compile(r"^Style: (Header|HeaderPlate),.*$", re.MULTILINE)


def _alignment_and_margin_v(style_line: str) -> tuple[int, int]:
    fields = style_line.split(",")
    # Format ends: ..., Alignment, MarginL, MarginR, MarginV, Encoding
    return int(fields[-5]), int(fields[-2])


def _ink_rows(img: Image.Image) -> tuple[int, int]:
    """Top and bottom (exclusive) rows of the non-transparent pixels."""
    bbox = img.getchannel("A").getbbox()
    assert bbox is not None, "header rendered nothing"
    return bbox[1], bbox[3]


@pytest.fixture
def text_font_only(monkeypatch):
    """Render text-only headers through the PNG path with just a text font.

    The PNG path needs a color-emoji font only to draw emoji. CI has a text
    font (DejaVu/Liberation) but no color-emoji font, so the emoji font slot
    reuses the text font: these headers contain no emoji.
    """
    header_image._resolve_text_font.cache_clear()
    path = header_image._resolve_text_font()
    if path is None:
        pytest.skip("no Pillow-compatible text font on this host")
    monkeypatch.setattr(
        header_image, "_require_header_fonts", lambda: (path, (path, 42))
    )
    yield
    header_image._resolve_text_font.cache_clear()


def _render_png(tmp_path, text: str, style: StyleConfig) -> tuple[int, int]:
    out = header_image.render_header_png(text, tmp_path / "header.png", style)
    return _ink_rows(Image.open(out).convert("RGBA"))


# --- the value --------------------------------------------------------------


def test_header_margin_v_is_pinned_near_eleven_percent():
    assert StyleConfig().header_margin_v == HEADER_TOP_PX
    for name in HEADER_STYLE_NAMES:
        assert style_for_presets("classic", name).header_margin_v == HEADER_TOP_PX


# --- ASS path ----------------------------------------------------------------


@pytest.mark.parametrize("name", HEADER_STYLE_NAMES)
def test_ass_header_styles_are_top_aligned_at_the_new_position(name):
    ass = build_ass(
        [], header="Hook", duration=1.0, style=style_for_presets("classic", name)
    )
    lines = [m.group(0) for m in _HEADER_STYLE_RE.finditer(ass)]
    assert lines, "no header style emitted"
    for line in lines:
        # Alignment 8 = top-center, so MarginV is the distance from the top.
        assert _alignment_and_margin_v(line) == (8, HEADER_TOP_PX)


# --- header-image path -------------------------------------------------------


def test_header_image_top_follows_header_margin_v(tmp_path, text_font_only):
    """The PNG path reads ``style.header_margin_v``, not a constant of its own."""
    plate = style_for_presets("classic", "black_plate")
    y_pad = max(4, plate.header_padding // 2)
    for margin_v in (HEADER_TOP_PX, 333):
        top, _ = _render_png(tmp_path, "Hook", replace(plate, header_margin_v=margin_v))
        assert top == margin_v - y_pad


@pytest.mark.parametrize("name", HEADER_STYLE_NAMES)
def test_header_image_sits_at_the_new_position(tmp_path, text_font_only, name):
    style = style_for_presets("classic", name)
    top, _ = _render_png(tmp_path, "Hook", style)
    # Plates start a few px above the margin; plain text ink a few px below.
    assert HEADER_TOP_PX - 12 <= top <= HEADER_TOP_PX + 20


# --- both paths agree (needs ffmpeg with libass; skipped in CI) -------------


def _libass_ink_rows(tmp_path, ass: str) -> tuple[int, int]:
    ass_path = tmp_path / "header.ass"
    ass_path.write_text(ass)
    frame = tmp_path / "frame.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x808080:s=1080x1920",
            "-frames:v",
            "1",
            "-vf",
            f"ass={ass_path}",
            str(frame),
        ],
        check=True,
        capture_output=True,
    )
    img = Image.open(frame).convert("RGB")
    diff = ImageChops.difference(img, Image.new("RGB", img.size, (128, 128, 128)))
    bbox = diff.convert("L").point(lambda v: 255 if v > 8 else 0).getbbox()
    assert bbox is not None, "libass rendered nothing"
    return bbox[1], bbox[3]


def _has_libass() -> bool:
    if shutil.which("ffmpeg") is None:
        return False
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True
    ).stdout
    return " ass " in out


@pytest.mark.skipif(not _has_libass(), reason="ffmpeg with libass is unavailable")
@pytest.mark.parametrize("name", HEADER_STYLE_NAMES)
def test_libass_and_header_image_put_the_header_top_in_the_same_place(
    tmp_path, text_font_only, name
):
    style = style_for_presets("classic", name)
    ass = build_ass([], header="Hook", captions_on=False, duration=1.0, style=style)
    libass_top, _ = _libass_ink_rows(tmp_path, ass)
    png_top, _ = _render_png(tmp_path, "Hook", style)
    assert HEADER_TOP_PX - 20 <= libass_top <= HEADER_TOP_PX + 12
    assert abs(libass_top - png_top) <= 12


# --- framing warning zone ----------------------------------------------------


def test_header_zone_is_derived_from_the_header_position():
    assert HEADER_ZONE_PX == StyleConfig().header_margin_v + framing.HEADER_BLOCK_MAX_PX


@pytest.mark.parametrize("name", HEADER_STYLE_NAMES)
def test_header_zone_covers_a_two_line_header_with_its_plate(
    tmp_path, text_font_only, name
):
    style = style_for_presets("classic", name)
    top, bottom = _render_png(tmp_path, TWO_LINE, style)
    assert 0 <= top and bottom <= HEADER_ZONE_PX
    if _has_libass():
        ass = build_ass(
            [], header=TWO_LINE, captions_on=False, duration=1.0, style=style
        )
        top, bottom = _libass_ink_rows(tmp_path, ass)
        assert 0 <= top and bottom <= HEADER_ZONE_PX


def _faces_with_output_top(output_top_px: float) -> list[TrackSample]:
    """A steady face whose box top lands at ``output_top_px`` in the 1920 frame."""
    source_h, face_h = 1080, 180
    top_src = output_top_px * source_h / 1920
    cy = top_src + face_h / 2
    return [TrackSample(t=i * 0.2, cx=960, cy=cy, w=140, h=face_h) for i in range(50)]


def test_face_behind_the_raised_header_warns():
    plan = plan_crop(_faces_with_output_top(HEADER_TOP_PX + 40), [], 1920, 1080)
    assert plan.warning == "header_zone"


def test_face_below_the_raised_header_no_longer_warns():
    # Inside the old 450 px zone, but clear of the header block at 210-370.
    plan = plan_crop(_faces_with_output_top(HEADER_ZONE_PX + 30), [], 1920, 1080)
    assert plan.warning is None
    assert framing.HEADER_ZONE_PX < 450
