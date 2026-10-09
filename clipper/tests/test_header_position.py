"""Issue #20: the header sits ~11% from the top.

Every header is drawn by ``render.header_image`` and overlaid (RiceSuite #65).
It reads ``StyleConfig.header_margin_v``, and the ingest face-warning zone in
``render.framing`` is derived from it. The editor re-checks the zone against
the clip's own header (``framing.header_warning``).
"""

from dataclasses import replace

import pytest

from app.models import TrackSample
from render import framing, text_image
from render.ass import HEADER_STYLE_NAMES, StyleConfig, style_for_presets
from render.framing import HEADER_ZONE_PX, plan_crop
from render.header_image import header_layer

HEADER_TOP_PX = 210  # ~11% of 1920; Issue #20 (was 450, ~23%)
TWO_LINE = "Two lines of header text\nwith a second line under it"


@pytest.fixture
def text_font():
    text_image.clear_font_caches()
    if text_image._resolve_text_font() is None:
        pytest.skip("no Pillow-compatible text font on this host")
    yield
    text_image.clear_font_caches()


def _ink_rows(text: str, style: StyleConfig) -> tuple[int, int]:
    box = header_layer(text, style).box
    assert box is not None, "header rendered nothing"
    return box.top, box.bottom


# --- the value --------------------------------------------------------------


def test_header_margin_v_is_pinned_near_eleven_percent():
    assert StyleConfig().header_margin_v == HEADER_TOP_PX
    for name in HEADER_STYLE_NAMES:
        assert style_for_presets("montserrat", name).header_margin_v == HEADER_TOP_PX


# --- header-image path -------------------------------------------------------


def test_header_image_top_follows_header_margin_v(text_font):
    """The PNG path reads ``style.header_margin_v``, not a constant of its own."""
    plate = style_for_presets("montserrat", "black_plate")
    for margin_v in (HEADER_TOP_PX, 333):
        top, _ = _ink_rows("Hook", replace(plate, header_margin_v=margin_v))
        assert top == margin_v - plate.header_padding


@pytest.mark.parametrize("name", HEADER_STYLE_NAMES)
def test_header_image_sits_at_the_new_position(text_font, name):
    style = style_for_presets("montserrat", name)
    top, _ = _ink_rows("Hook", style)
    # Plates start 16 px above the margin; plain text ink a few px below.
    assert HEADER_TOP_PX - 16 <= top <= HEADER_TOP_PX + 12


# --- framing warning zone ----------------------------------------------------


def test_header_zone_is_derived_from_the_header_position():
    assert HEADER_ZONE_PX == StyleConfig().header_margin_v + framing.HEADER_BLOCK_MAX_PX
    assert framing.DEFAULT_HEADER_SPAN == (HEADER_TOP_PX, HEADER_ZONE_PX)


@pytest.mark.parametrize("name", HEADER_STYLE_NAMES)
def test_header_zone_covers_a_two_line_header_with_its_plate(text_font, name):
    style = style_for_presets("montserrat", name)
    top, bottom = _ink_rows(TWO_LINE, style)
    assert HEADER_TOP_PX - 16 <= top and bottom <= HEADER_ZONE_PX


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
