"""Render the on-screen header to a full-frame transparent PNG.

Every non-empty header is drawn here with Pillow (``render.text_image``) and
composited by ffmpeg's ``overlay``, with or without emoji (RiceSuite #65).
libass draws only the captions, plus a minimal text header when this module
fails (see ``render.pipeline``).

The header's look comes from ``StyleConfig``'s ``header_*`` fields. Its top
is ``header_margin_v`` (the top of the first line box, 210 px by default:
RiceSuite #20). The block is moved, if needed, so that it stays inside the
frame and above the caption zone.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from render.ass import StyleConfig
from render.framing import CAPTION_ZONE_PX
from render.text_image import TextFontError, TextLook, draw_block

# Same side margins as the libass header had (MarginL/MarginR 80).
SIDE_MARGIN_PX = 80

# Kept for callers that catch the old name.
HeaderFontError = TextFontError


@dataclass(frozen=True)
class HeaderBox:
    """The header's drawn extent in output pixels (plate, outline, shadow)."""

    left: int
    top: int
    right: int
    bottom: int


@dataclass
class HeaderLayer:
    image: Image.Image  # full-frame RGBA
    box: HeaderBox | None  # None for an empty header


def text_look(style: StyleConfig) -> TextLook:
    """The ``TextLook`` the header fields of ``style`` describe."""
    return TextLook(
        font=style.header_font,
        size=style.header_font_size,
        color=style.header_color,
        outline=style.header_outline,
        outline_color=style.header_outline_color,
        shadow=style.header_shadow,
        plate=style.header_plate,
        plate_color=style.header_plate_color,
        plate_opacity=style.header_plate_opacity,
        plate_radius=style.header_plate_radius,
        plate_padding=style.header_padding,
        align=style.header_align,
        line_spacing=style.header_line_spacing,
    )


def header_layer(
    text: str,
    style: StyleConfig | None = None,
    canvas: tuple[int, int] = (1080, 1920),
) -> HeaderLayer:
    """Draw ``text`` into a transparent full-frame image.

    The first line box's top sits at ``style.header_margin_v``. If the drawn
    block would run into the caption zone it moves up; if it would leave the
    top of the frame it moves down (the top wins when both apply).
    """
    style = style or StyleConfig()
    cw, ch = canvas
    frame = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    text = (text or "").strip()
    if not text:
        return HeaderLayer(frame, None)

    block = draw_block(text, text_look(style), max_width=cw - 2 * SIDE_MARGIN_PX)
    ox, oy = block.origin
    ink = block.image.getchannel("A").getbbox() or (ox, oy, ox, oy)

    if style.header_align == "left":
        x = SIDE_MARGIN_PX
    elif style.header_align == "right":
        x = cw - SIDE_MARGIN_PX - block.width
    else:
        x = (cw - block.width) // 2

    y = style.header_margin_v
    caption_top = ch - CAPTION_ZONE_PX
    bottom = y - oy + ink[3]
    if bottom > caption_top:
        y -= bottom - caption_top
    top = y - oy + ink[1]
    if top < 0:
        y -= top

    left = x - ox
    upper = y - oy
    frame.alpha_composite(block.image, _clip_dest(left, upper), _clip_src(left, upper))
    box = HeaderBox(
        left=max(0, left + ink[0]),
        top=max(0, upper + ink[1]),
        right=min(cw, left + ink[2]),
        bottom=min(ch, upper + ink[3]),
    )
    return HeaderLayer(frame, box)


def _clip_dest(left: int, upper: int) -> tuple[int, int]:
    return max(0, left), max(0, upper)


def _clip_src(left: int, upper: int) -> tuple[int, int]:
    return max(0, -left), max(0, -upper)


def render_header_png(
    text: str,
    out_path: str | Path,
    style: StyleConfig | None = None,
    canvas: tuple[int, int] = (1080, 1920),
) -> HeaderBox | None:
    """Write the header layer to ``out_path``; return its drawn box."""
    layer = header_layer(text, style, canvas)
    layer.image.save(Path(out_path), format="PNG")
    return layer.box


def header_png_bytes(
    text: str,
    style: StyleConfig | None = None,
    canvas: tuple[int, int] = (1080, 1920),
) -> tuple[bytes, HeaderBox | None]:
    """The header layer as PNG bytes, for the editor preview (no file written)."""
    layer = header_layer(text, style, canvas)
    buf = io.BytesIO()
    layer.image.save(buf, format="PNG")
    return buf.getvalue(), layer.box
