"""Draw text and colour emoji to transparent images with Pillow.

Shared by the header (RiceSuite #65) and, later, the caption emoji rows
(RiceSuite #66). libass on the macOS/CoreText toolchain draws colour emoji as
empty boxes (docs/spikes/emoji-burn-in.md), so anything that may carry emoji is
drawn here and composited with ffmpeg's ``overlay``.

This module owns font resolution (the curated font list, system candidates,
fontconfig, and the colour-emoji probe), the libass-compatible font scale,
balanced line wrapping, and block drawing: plate, soft shadow, outline, fill.

The installed Pillow has no raqm, so text gets basic layout (``kern`` table
kerning, no HarfBuzz shaping). Blocks are drawn at ``SUPERSAMPLE`` times their
size and scaled down, which keeps hinted glyph advances from adding up to a
wider line than libass draws.
"""

from __future__ import annotations

import math
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from functools import cache, lru_cache
from itertools import pairwise
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# Emoji codepoint ranges (covers the common blocks incl. the real header
# examples 🥹 U+1F979 and 😂 U+1F602, plus ZWJ sequences and variation selectors).
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff"
    "\U00002300-\U000023ff\U00002b00-\U00002bff\U0000fe00-\U0000fe0f\U0000200d]"
)

# Open-licence fonts committed with Clipper. RiceSuite #66 adds the first one.
BUNDLED_FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Checked in order; the first existing file wins. macOS system fonts come first,
# then the common Linux package locations (Debian/Ubuntu, Fedora, Arch).
_TEXT_FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/System/Library/Fonts/HelveticaNeue.ttc",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans/LiberationSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]
# Color-emoji fonts are bitmap (sbix/CBDT) with fixed strikes — Pillow can only
# load them at a valid strike size, and only some render non-empty glyphs.
# Apple Color Emoji (sbix) renders reliably in Pillow; the Homebrew Noto build is
# COLRv1/vector and rasterizes blank here, so Apple is tried first on macOS. The
# Linux distro packages of Noto Color Emoji ship the CBDT bitmap build. Every
# candidate is still probed by ``_resolve_emoji_font`` before it is used.
_EMOJI_FONT_CANDIDATES = [
    "/System/Library/Fonts/Apple Color Emoji.ttc",
    os.path.expanduser("~/Library/Fonts/NotoColorEmoji-Regular.ttf"),
    "/Library/Fonts/NotoColorEmoji-Regular.ttf",
    "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto-color-emoji-fonts/NotoColorEmoji.ttf",
    "/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",
    "/usr/share/fonts/noto/NotoColorEmoji.ttf",
]
_LOADABLE_FONT_SUFFIXES = (".ttf", ".ttc", ".otf")
_FONTCONFIG_TIMEOUT_S = 5
# Candidate strike sizes, largest first (rendered high-res then scaled down).
_EMOJI_STRIKES = [160, 137, 136, 128, 96, 109, 64]

# Blocks are drawn this many times larger, then scaled down (see module doc).
SUPERSAMPLE = 4
# An emoji is drawn this much taller than the font's em, close to how a
# colour-emoji glyph sits beside text in the same font.
EMOJI_EM_SCALE = 1.15


class TextFontError(RuntimeError):
    """A font needed to draw the text is missing on this host."""


@dataclass(frozen=True)
class FontChoice:
    """One entry in the curated font list.

    ``faces`` are ``(file, face name)`` candidates; a ``None`` face name takes
    the file's first face. ``ass_family`` is the libass family name, used only by
    the libass fallback header.
    """

    label: str
    ass_family: str
    faces: tuple[tuple[str, str | None], ...]


_SUPPLEMENTAL = "/System/Library/Fonts/Supplemental"

# The curated list. "arial" is the default and falls back to the generic bold
# sans chain; any other choice that is missing on this host uses the default.
FONT_CHOICES: dict[str, FontChoice] = {
    "arial": FontChoice("Arial Bold", "Arial", ()),
    "helvetica": FontChoice(
        "Helvetica Neue Bold",
        "Helvetica Neue",
        (("/System/Library/Fonts/HelveticaNeue.ttc", "Bold"),),
    ),
    "avenir": FontChoice(
        "Avenir Next Heavy",
        "Avenir Next",
        (("/System/Library/Fonts/Avenir Next.ttc", "Heavy"),),
    ),
    "futura": FontChoice(
        "Futura Bold",
        "Futura",
        (("/System/Library/Fonts/Supplemental/Futura.ttc", "Bold"),),
    ),
    "impact": FontChoice("Impact", "Impact", ((f"{_SUPPLEMENTAL}/Impact.ttf", None),)),
    "arial_black": FontChoice(
        "Arial Black", "Arial Black", ((f"{_SUPPLEMENTAL}/Arial Black.ttf", None),)
    ),
    "din": FontChoice(
        "DIN Condensed Bold",
        "DIN Condensed",
        ((f"{_SUPPLEMENTAL}/DIN Condensed Bold.ttf", None),),
    ),
    "georgia": FontChoice(
        "Georgia Bold", "Georgia", ((f"{_SUPPLEMENTAL}/Georgia Bold.ttf", None),)
    ),
}
DEFAULT_FONT = "arial"


@dataclass(frozen=True)
class FontRef:
    """A loadable font face: a file plus its index inside a collection."""

    path: str
    index: int = 0


# --- detection + segmentation ------------------------------------------------


def has_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text or ""))


def segment(word: str) -> list[tuple[str, str]]:
    """Split a word into consecutive ('text'|'emoji', chunk) runs."""
    runs: list[tuple[str, str]] = []
    for ch in word:
        kind = "emoji" if _EMOJI_RE.match(ch) else "text"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    return runs


# --- font resolution ---------------------------------------------------------


def _first_existing(paths: list[str]) -> str | None:
    return next((p for p in paths if os.path.exists(p)), None)


def _fontconfig_files(*args: str) -> list[str]:
    """Font file paths printed by a fontconfig tool, or [] if it is unavailable.

    Fallback for hosts whose fonts live outside the fixed candidate paths.
    ``args`` is the tool name plus its pattern; output is one path per line.
    """
    exe = shutil.which(args[0])
    if exe is None:
        return []
    try:
        out = subprocess.run(
            [exe, "-f", "%{file}\\n", *args[1:]],
            capture_output=True,
            text=True,
            timeout=_FONTCONFIG_TIMEOUT_S,
            check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [
        line.strip()
        for line in out.splitlines()
        if line.strip().lower().endswith(_LOADABLE_FONT_SUFFIXES)
    ]


@lru_cache(maxsize=1)
def _resolve_text_font() -> str | None:
    """Path of the default text font: a known candidate, else fontconfig's pick."""
    found = _first_existing(_TEXT_FONT_CANDIDATES)
    if found:
        return found
    matches = _fontconfig_files("fc-match", "sans-serif:bold")
    return next((p for p in matches if os.path.exists(p)), None)


def _face_index(path: str, face: str | None) -> int | None:
    """Index of the named face inside ``path``, or None when it is not there."""
    if face is None:
        return 0
    index = 0
    while True:
        try:
            loaded = ImageFont.truetype(path, 12, index=index)
        except OSError:
            return None
        if loaded.getname()[1] == face:
            return index
        index += 1


@cache
def _resolve_choice(key: str) -> FontRef | None:
    choice = FONT_CHOICES.get(key)
    if choice is None:
        return None
    for path, face in choice.faces:
        if os.path.exists(path):
            index = _face_index(path, face)
            if index is not None:
                return FontRef(path, index)
    return None


def font_available(key: str) -> bool:
    """True when ``key`` resolves to its own face rather than the default."""
    if key == DEFAULT_FONT:
        return _resolve_text_font() is not None
    return _resolve_choice(key) is not None


def resolve_font(key: str) -> FontRef:
    """The face for curated font ``key``, else the default text font.

    Raises :class:`TextFontError` when not even the default resolves.
    """
    ref = _resolve_choice(key) if key != DEFAULT_FONT else None
    if ref is not None:
        return ref
    path = _resolve_text_font()
    if path is None:
        raise TextFontError(
            "missing a text font for the header: install Liberation Sans or "
            "DejaVu Sans (e.g. fonts-liberation)"
        )
    return FontRef(path)


def clear_font_caches() -> None:
    """Forget resolved fonts (tests swap the candidate files)."""
    _resolve_text_font.cache_clear()
    _resolve_choice.cache_clear()
    _resolve_emoji_font.cache_clear()
    _load.cache_clear()


def _emoji_font_paths() -> list[str]:
    """Existing color-emoji font files, known candidates first, then fontconfig."""
    paths = [p for p in _EMOJI_FONT_CANDIDATES if os.path.exists(p)]
    for p in sorted(_fontconfig_files("fc-list", ":color=true")):
        if p not in paths and os.path.exists(p):
            paths.append(p)
    return paths


@lru_cache(maxsize=1)
def _resolve_emoji_font() -> tuple[str, int] | None:  # pragma: no cover
    """Find a (font path, strike size) that actually renders a color glyph.

    Excluded from coverage: this requires a real Pillow-renderable color-emoji
    font (Apple Color Emoji on macOS, or the CBDT Noto Color Emoji that Linux
    distros package). The Homebrew Noto build rasterizes blank (see
    docs/spikes/emoji-burn-in.md), and the CI runner is not guaranteed to have
    any such font. It is integration-tested via test_text_image.py, which
    skips when no such font is present.
    """
    probe = "\U0001f602"
    for path in _emoji_font_paths():
        for size in _EMOJI_STRIKES:
            try:
                font = ImageFont.truetype(path, size)
                im = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
                ImageDraw.Draw(im).text((0, 0), probe, font=font, embedded_color=True)
                if im.getbbox() is not None:
                    return path, size
            except Exception:
                continue
    return None


def require_emoji_font() -> tuple[str, int]:
    """The colour-emoji (path, strike), or a :class:`TextFontError`."""
    resolved = _resolve_emoji_font()
    if resolved is None:
        raise TextFontError(
            "missing a renderable color-emoji font: install Noto Color Emoji "
            "(e.g. fonts-noto-color-emoji), or remove the emoji"
        )
    return resolved


@lru_cache(maxsize=32)
def _load(ref: FontRef, size: float) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(ref.path, size, index=ref.index)


def ass_font(ref: FontRef, size: float) -> ImageFont.FreeTypeFont:
    """Load ``ref`` at the scale libass uses for an ASS ``Fontsize`` of ``size``.

    libass (like VSFilter) sizes a font so its ascent plus descent equals the
    ASS size; Pillow's size is the em. Scaling by the font's own metrics keeps a
    Pillow header the same size as the libass header it replaces.
    """
    ascent, descent = _load(ref, 1000).getmetrics()
    return _load(ref, size * 1000 / max(1, ascent + descent))


# --- emoji -------------------------------------------------------------------


def emoji_image(cluster: str, height: int) -> Image.Image:  # pragma: no cover
    """Render an emoji cluster at the native strike, cropped and scaled to ``height``.

    Excluded from coverage: reachable only with a real color-emoji font, which
    the Linux CI runner lacks (see ``_resolve_emoji_font``).
    """
    path, strike = require_emoji_font()
    emoji_font = _load(FontRef(path), strike)
    box = strike * (len(cluster) + 2)
    tmp = Image.new("RGBA", (box, strike * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((0, 0), cluster, font=emoji_font, embedded_color=True)
    bbox = tmp.getbbox()
    if bbox:
        tmp = tmp.crop(bbox)
    if tmp.height:
        w = max(1, round(tmp.width * height / tmp.height))
        tmp = tmp.resize((w, height), Image.LANCZOS)
    return tmp


def compose_row(images: Sequence[Image.Image], gap: int) -> Image.Image:
    """Lay images side by side, vertically centred, ``gap`` px apart.

    The caption emoji row (RiceSuite #66) centres the result over a phrase.
    """
    if not images:
        return Image.new("RGBA", (1, 1), (0, 0, 0, 0))
    width = sum(im.width for im in images) + gap * (len(images) - 1)
    height = max(im.height for im in images)
    row = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    x = 0
    for im in images:
        row.alpha_composite(im.convert("RGBA"), (x, (height - im.height) // 2))
        x += im.width + gap
    return row


def emoji_row(clusters: Sequence[str], height: int, gap: int) -> Image.Image:
    """A row of emoji clusters at ``height`` px, ``gap`` px apart."""
    return compose_row([emoji_image(c, height) for c in clusters], gap)


# --- wrapping ----------------------------------------------------------------


def wrap(
    text: str,
    measure_word: Callable[[str], tuple[object, float]],
    space_w: float,
    max_width: float,
) -> list[tuple[list, float]]:
    """Wrap ``text`` into lines the way libass ``WrapStyle: 0`` does.

    Explicit newlines are hard breaks. Within a paragraph, words fill lines
    greedily, then each soft break moves left while that makes the two lines it
    separates closer in width (libass's "smart" wrap, upper line wider).

    ``measure_word(word) -> (parts, width)``. Returns ``(parts_list, width)``
    per line, where ``parts_list`` holds ``(parts, width)`` per word. Pure: the
    caller supplies the measurer, so this is testable without fonts.
    """
    lines: list[tuple[list, float]] = []
    for paragraph in text.split("\n"):
        words = [measure_word(w) for w in paragraph.split(" ") if w]
        if not words:
            continue
        # Greedy pass: break indices into ``words``.
        breaks: list[int] = []
        cur_w = 0.0
        for i, (_parts, w) in enumerate(words):
            start = breaks[-1] if breaks else 0
            add = w if i == start else space_w + w
            if i > start and cur_w + add > max_width:
                breaks.append(i)
                cur_w = w
            else:
                cur_w += add
        bounds = [0, *breaks, len(words)]

        def width(lo: int, hi: int, words=words) -> float:
            seg = words[lo:hi]
            return sum(w for _p, w in seg) + space_w * max(0, len(seg) - 1)

        # Rebalance: move a break left while it narrows the gap between lines.
        moved = True
        while moved:
            moved = False
            for k in range(1, len(bounds) - 1):
                lo, mid, hi = bounds[k - 1], bounds[k], bounds[k + 1]
                if mid - 1 <= lo:
                    continue
                before = abs(width(lo, mid) - width(mid, hi))
                after = abs(width(lo, mid - 1) - width(mid - 1, hi))
                if after < before and width(mid - 1, hi) <= max_width:
                    bounds[k] = mid - 1
                    moved = True
        for lo, hi in pairwise(bounds):
            lines.append((words[lo:hi], width(lo, hi)))
    return lines


# --- block drawing -----------------------------------------------------------


@dataclass(frozen=True)
class TextLook:
    """How a block of text is drawn. Colours are ``RRGGBB``."""

    font: str = DEFAULT_FONT
    size: int = 42  # ASS-equivalent size: one line box is ``size`` px tall
    color: str = "FFFFFF"
    outline: int = 2
    outline_color: str = "000000"
    shadow: bool = False
    plate: str = "none"  # "none" | "solid" | "translucent"
    plate_color: str = "000000"
    plate_opacity: int = 75  # percent, for a translucent plate
    plate_radius: int = 0
    plate_padding: int = 16
    align: str = "center"  # "left" | "center" | "right"
    line_spacing: float = 1.0


def _rgb(hex6: str) -> tuple[int, int, int]:
    return (int(hex6[0:2], 16), int(hex6[2:4], 16), int(hex6[4:6], 16))


@dataclass
class Block:
    """A drawn text block.

    ``image`` is the block at output scale. ``origin`` is where the top-left
    of the first line box sits inside ``image``: place the image at
    ``(x - origin[0], y - origin[1])`` to put that corner at ``(x, y)``.
    ``width`` is the widest line; ``height`` runs from the first line box
    top to the last line box bottom. ``line_widths`` and ``line_tops`` are
    relative to that corner.
    """

    image: Image.Image
    origin: tuple[int, int]
    width: int
    height: int
    line_widths: list[float]
    line_tops: list[float]


def shadow_offset(look: TextLook) -> tuple[int, int, int]:
    """(dx, dy, blur radius) of the soft shadow, scaled with the text size."""
    return (
        max(1, round(look.size * 0.05)),
        max(2, round(look.size * 0.08)),
        max(2, round(look.size * 0.12)),
    )


def draw_block(text: str, look: TextLook, max_width: float) -> Block:
    """Draw ``text`` with ``look``, wrapped to ``max_width`` px.

    Each line box is ``look.size`` px tall (the libass line height) and lines
    are ``look.size * look.line_spacing`` apart. Lines align left, centre, or
    right inside the widest line. A plate is the union of one padded box per
    line, so overlapping boxes do not darken. The block is drawn at
    ``SUPERSAMPLE`` scale and scaled down.
    """
    k = SUPERSAMPLE
    ref = resolve_font(look.font)
    font = ass_font(ref, look.size * k)
    ascent, descent = font.getmetrics()
    line_h = ascent + descent  # == size * k, give or take rounding
    pitch = look.size * look.line_spacing * k
    space_w = font.getlength(" ")
    em = font.size
    emoji_h = round(em * EMOJI_EM_SCALE)

    def measure_word(word: str):
        parts = []
        width = 0.0
        for kind, chunk in segment(word):
            if kind == "emoji":
                img = emoji_image(chunk, emoji_h)
                parts.append(("emoji", img, img.width))
                width += img.width
            else:
                w = font.getlength(chunk)
                parts.append(("text", chunk, w))
                width += w
        return parts, width

    lines = wrap(text, measure_word, space_w, max_width * k)
    block_w = max((w for _p, w in lines), default=0.0)
    block_h = (len(lines) - 1) * pitch + line_h if lines else 0

    # Margin around the line boxes for the plate, outline, and shadow.
    pad = look.plate_padding * k if look.plate != "none" else 0
    stroke = look.outline * k
    dx, dy, blur = (v * k for v in shadow_offset(look)) if look.shadow else (0, 0, 0)
    margin = math.ceil(max(pad, stroke) + max(dx, dy) + 2 * blur) + 2 * k
    margin = (margin + k - 1) // k * k  # whole output pixels

    canvas_w = math.ceil(block_w) + 2 * margin
    canvas_h = math.ceil(block_h) + 2 * margin
    canvas_w = (canvas_w + k - 1) // k * k
    canvas_h = (canvas_h + k - 1) // k * k

    line_xs: list[float] = []
    line_tops: list[float] = []
    for i, (_parts, line_w) in enumerate(lines):
        if look.align == "left":
            lx = 0.0
        elif look.align == "right":
            lx = block_w - line_w
        else:
            lx = (block_w - line_w) / 2
        line_xs.append(margin + lx)
        line_tops.append(margin + i * pitch)

    out = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))

    if look.plate != "none" and lines:
        mask = Image.new("L", out.size, 0)
        mdraw = ImageDraw.Draw(mask)
        for (_parts, line_w), lx, ty in zip(lines, line_xs, line_tops, strict=True):
            mdraw.rounded_rectangle(
                [lx - pad, ty - pad, lx + line_w + pad, ty + line_h + pad],
                radius=look.plate_radius * k,
                fill=255,
            )
        alpha = 255 if look.plate == "solid" else round(look.plate_opacity * 2.55)
        mask = mask.point(lambda v: v * alpha // 255)
        plate = Image.new("RGBA", out.size, (*_rgb(look.plate_color), 0))
        plate.putalpha(mask)
        out.alpha_composite(plate)

    ink = Image.new("RGBA", out.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(ink)
    fill = (*_rgb(look.color), 255)
    stroke_fill = (*_rgb(look.outline_color), 255)
    for (parts_list, _w), lx, ty in zip(lines, line_xs, line_tops, strict=True):
        x = lx
        for n, (parts, _ww) in enumerate(parts_list):
            if n:
                x += space_w
            for part in parts:
                if part[0] == "emoji":  # pragma: no cover - needs an emoji font
                    img = part[1]
                    ink.alpha_composite(
                        img, (round(x), round(ty + (line_h - img.height) / 2))
                    )
                else:
                    draw.text(
                        (x, ty + ascent),
                        part[1],
                        font=font,
                        anchor="ls",
                        fill=fill,
                        stroke_width=stroke,
                        stroke_fill=stroke_fill,
                    )
                x += part[2]

    if look.shadow:
        silhouette = ink.getchannel("A").point(lambda v: v * 150 // 255)
        silhouette = silhouette.filter(ImageFilter.GaussianBlur(blur))
        shadow = Image.new("RGBA", out.size, (0, 0, 0, 0))
        shadow.putalpha(silhouette)
        out.alpha_composite(shadow, (dx, dy))
    out.alpha_composite(ink)

    # Scale down in premultiplied alpha so plate edges keep their colour.
    small = (
        out.convert("RGBa")
        .resize((canvas_w // k, canvas_h // k), Image.BOX)
        .convert("RGBA")
    )
    return Block(
        image=small,
        origin=(margin // k, margin // k),
        width=math.ceil(block_w / k),
        height=math.ceil(block_h / k),
        line_widths=[w / k for _p, w in lines],
        line_tops=[(t - margin) / k for t in line_tops],
    )
