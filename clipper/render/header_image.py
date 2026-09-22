"""Render the on-screen header (text + color emoji) to a transparent PNG.

Why this exists: libass on the macOS/CoreText toolchain cannot render color
emoji — it falls back to empty ".LastResort" boxes (see
docs/spikes/emoji-burn-in.md). So when a header contains emoji we bypass libass
for the header and burn it via an image ``overlay`` instead. Pillow renders the
text and color emoji (preferring Apple Color Emoji on macOS), which ffmpeg then
composites.

Headers with no emoji never touch this module — they stay on the libass path.
Captions always stay on libass (they carry no emoji). The selected header
treatment is shared with the libass path: plain text has no plate, while the
two plate choices draw a per-line legibility box.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from render.ass import StyleConfig

# Emoji codepoint ranges (covers the common blocks incl. the real header
# examples 🥹 U+1F979 and 😂 U+1F602, plus ZWJ sequences and variation selectors).
_EMOJI_RE = re.compile(
    "[\U0001f000-\U0001faff\U00002600-\U000027bf\U0001f1e6-\U0001f1ff"
    "\U00002300-\U000023ff\U00002b00-\U00002bff\U0000fe00-\U0000fe0f\U0000200d]"
)

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


def has_emoji(text: str) -> bool:
    return bool(_EMOJI_RE.search(text or ""))


class HeaderFontError(RuntimeError):
    pass


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
    """Path of the header text font: a known candidate, else fontconfig's pick."""
    found = _first_existing(_TEXT_FONT_CANDIDATES)
    if found:
        return found
    matches = _fontconfig_files("fc-match", "sans-serif:bold")
    return next((p for p in matches if os.path.exists(p)), None)


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
    any such font. It is integration-tested via test_header_image.py, which
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


def _require_header_fonts() -> tuple[str, tuple[str, int]]:
    """Resolve both header fonts or raise a ``HeaderFontError`` saying which is missing."""
    text_font_path = _resolve_text_font()
    if not text_font_path:
        raise HeaderFontError(
            "missing a text font for the emoji header: install Liberation Sans or "
            "DejaVu Sans (e.g. fonts-liberation), or remove the emoji"
        )
    resolved = _resolve_emoji_font()
    if resolved is None:
        raise HeaderFontError(
            "missing a renderable color-emoji font for the emoji header: install "
            "Noto Color Emoji (e.g. fonts-noto-color-emoji), or remove the emoji"
        )
    return text_font_path, resolved


def _segment(word: str) -> list[tuple[str, str]]:
    """Split a word into consecutive ('text'|'emoji', chunk) runs."""
    runs: list[tuple[str, str]] = []
    for ch in word:
        kind = "emoji" if _EMOJI_RE.match(ch) else "text"
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1] + ch)
        else:
            runs.append((kind, ch))
    return runs


def _render_emoji(
    cluster: str, size: int, emoji_font: ImageFont.FreeTypeFont
) -> Image.Image:  # pragma: no cover
    """Render an emoji cluster at the native strike, cropped and scaled to ``size``.

    Excluded from coverage: reachable only with a real color-emoji font, which
    the Linux CI runner lacks (see ``_resolve_emoji_font``).
    """
    strike = emoji_font.size
    box = strike * (len(cluster) + 2)
    tmp = Image.new("RGBA", (box, strike * 2), (0, 0, 0, 0))
    ImageDraw.Draw(tmp).text((0, 0), cluster, font=emoji_font, embedded_color=True)
    bbox = tmp.getbbox()
    if bbox:
        tmp = tmp.crop(bbox)
    if tmp.height:
        w = max(1, round(tmp.width * size / tmp.height))
        tmp = tmp.resize((w, size), Image.LANCZOS)
    return tmp


def _wrap(text, measure_word, space_w, max_width):
    """Greedy word-wrap into lines, honoring explicit newlines as hard breaks.

    ``measure_word(word) -> (parts, width)``. Returns a list of
    ``(parts_list, line_width)``. Pure: the caller supplies the measurer, so this
    is testable without fonts.
    """
    lines: list[tuple[list, float]] = []
    for paragraph in text.split("\n"):
        cur: list = []
        cur_w = 0.0
        for word in (w for w in paragraph.split(" ") if w):
            parts, w = measure_word(word)
            add = w if not cur else space_w + w
            if cur and cur_w + add > max_width:
                lines.append((cur, cur_w))
                cur, cur_w = [(parts, w)], w
            else:
                cur.append((parts, w))
                cur_w += add
        if cur:
            lines.append((cur, cur_w))
    return lines


def render_header_png(
    text: str,
    out_path: str | Path,
    style: StyleConfig | None = None,
    canvas: tuple[int, int] = (1080, 1920),
) -> Path:  # pragma: no cover
    """Render ``text`` (with color emoji) to a full-frame transparent PNG.

    The header block is near the top and horizontally centered — matching the
    libass header placement — so ffmpeg can overlay it at 0,0. Plain headers
    remain transparent everywhere except their text and edge.
    """
    style = style or StyleConfig()
    out_path = Path(out_path)
    text = (text or "").strip()

    text_font_path, resolved = _require_header_fonts()

    fs = style.header_font_size
    font = ImageFont.truetype(text_font_path, fs)
    emoji_font = ImageFont.truetype(resolved[0], resolved[1])
    emoji_size = round(fs * 1.05)

    cw, ch = canvas
    side_margin = 70
    max_width = cw - 2 * side_margin
    space_w = font.getlength(" ")

    # Measure a token (a whitespace-delimited word) as a list of drawable parts.
    def measure_word(word: str):
        parts = []
        width = 0.0
        for kind, chunk in _segment(word):
            if kind == "emoji":
                img = _render_emoji(chunk, emoji_size, emoji_font)
                parts.append(("emoji", img, img.width))
                width += img.width
            else:
                w = font.getlength(chunk)
                parts.append(("text", chunk, w))
                width += w
        return parts, width

    # Greedy word-wrap, honoring newlines the user typed in the 2-row textarea.
    lines = _wrap(text, measure_word, space_w, max_width)

    # Layout metrics.
    ascent, descent = font.getmetrics()
    text_h = ascent + descent
    line_h = round(max(text_h, emoji_size) * 1.35)
    x_pad = style.header_padding
    y_pad = max(4, style.header_padding // 2)

    img = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    plate_alpha = 255 - style.header_plate_alpha  # ASS alpha → Pillow alpha
    pr = int(style.header_plate_color[0:2], 16)
    pg = int(style.header_plate_color[2:4], 16)
    pb = int(style.header_plate_color[4:6], 16)
    plate = (pr, pg, pb, plate_alpha)
    text_color = (*(int(style.header_color[i : i + 2], 16) for i in (0, 2, 4)), 255)
    outline_color = (
        *(int(style.header_outline_color[i : i + 2], 16) for i in (0, 2, 4)),
        255,
    )

    y = style.header_margin_v
    for parts_list, line_w in lines:
        line_w = int(line_w)
        x0 = (cw - line_w) // 2
        if style.header_border_style == 3:
            # Per-line legibility plate. The libass opaque-box path is
            # rectangular; slight rounding keeps the emoji path friendly
            # without changing its size or placement.
            draw.rounded_rectangle(
                [x0 - x_pad, y - y_pad, x0 + line_w + x_pad, y + line_h + y_pad],
                radius=14,
                fill=plate,
            )
        # Draw the runs left to right.
        x = x0
        first = True
        for parts, _w in parts_list:
            if not first:
                x += space_w
            first = False
            for part in parts:
                if part[0] == "emoji":
                    emoji_img = part[1]
                    ey = y + (line_h - emoji_img.height) // 2
                    img.paste(emoji_img, (int(x), int(ey)), emoji_img)
                    x += part[2]
                else:
                    ty = y + (line_h - text_h) // 2
                    draw.text(
                        (x, ty),
                        part[1],
                        font=font,
                        fill=text_color,
                        stroke_width=max(0, style.header_outline),
                        stroke_fill=outline_color,
                    )
                    x += part[2]
        y += line_h + y_pad * 2

    img.save(out_path)
    return out_path
