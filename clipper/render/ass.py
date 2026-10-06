"""Build an ASS subtitle script for RiceClipper.

Emits the caption layer described in SPEC.md §4-5:

* **Captions** (§5, Tier-1) — ~4-5 word phrase blocks with a per-word colour
  highlight synced to the word timestamps. Implemented as one Dialogue event per
  active-word window; the whole phrase stays on screen while the highlight walks
  across it. Entirely native to libass (no scripting/second engine).

The header (§6) is drawn by Pillow (``render.header_image``) and overlaid by
ffmpeg (RiceSuite #65). This script carries a header only as the fallback when
that PNG render fails: a minimal header in the chosen font, size, colour,
outline, plate, alignment, and position, with square corners and no shadow.

The module is pure standard library and side-effect free, so ASS generation is
unit-testable without ffmpeg. ``StyleConfig`` is the parameterised template: the
Wave-2 "caption style/position config" (SPEC.md §7, D11) is filling these
variables from the UI, not rebuilding this file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from transcribe.phrasing import Phrase, WordLike, group_words


@dataclass
class StyleConfig:
    # Canvas — matches the 1080x1920 export target.
    play_res_x: int = 1080
    play_res_y: int = 1920

    # Caption text (the signature word-highlight look).
    font: str = "Arial"
    font_size: int = 96
    bold: bool = True
    italic: bool = False
    primary_color: str = "FFFFFF"  # base word colour, RRGGBB
    highlight_color: str = "35E36B"  # active word colour, RRGGBB
    outline_color: str = "000000"
    outline: int = 6  # thick outline for legibility on any bg
    shadow: int = 3
    caption_margin_v: int = 340  # px up from the bottom (lower third)

    # Header (drawn by Pillow; RiceSuite #65). Sizes are ASS-equivalent px.
    header_font: str = "arial"  # key into render.text_image.FONT_CHOICES
    header_font_size: int = 42
    header_color: str = "FFFFFF"
    header_outline: int = 2
    header_outline_color: str = "000000"
    header_shadow: bool = False  # soft blurred drop shadow
    header_plate: str = "none"  # "none" | "solid" | "translucent"
    header_plate_color: str = "000000"
    header_plate_opacity: int = 75  # percent, for a translucent plate
    header_plate_radius: int = 0
    header_padding: int = 16  # plate padding around each line
    header_align: str = "center"  # "left" | "center" | "right"
    header_line_spacing: float = 1.0
    header_margin_v: int = 210  # first line top, px from the top (Issue #20)


# The bottom band of the 1920 px frame kept for captions (render.framing
# re-exports it). The header is kept above it (RiceSuite #65).
CAPTION_ZONE_PX = 540

CAPTION_STYLE_NAMES = (
    "classic",
    "clean",
    "punch",
    "friendly",
    "sunset",
    "mono",
    "editorial",
    "lyric_block",
    "velvet_serif",
    "din_condensed",
    "baskerville",
)
HEADER_STYLE_NAMES = ("plain", "black_plate", "white_plate")


# These presets deliberately stay within ASS/libass Tier 1. Font families are
# common macOS fonts and libass/fontconfig can substitute them on other hosts.
# ``classic`` is the original v1 caption treatment and remains selectable.
_CAPTION_PRESETS: dict[str, dict[str, object]] = {
    "classic": {},
    "clean": {
        "font": "Helvetica Neue",
        "font_size": 88,
        "primary_color": "F8FAFC",
        "highlight_color": "F59E0B",
        "outline": 5,
        "shadow": 2,
        "caption_margin_v": 340,
    },
    "punch": {
        "font": "Impact",
        "font_size": 92,
        "primary_color": "FFFFFF",
        "highlight_color": "FF3B81",
        "outline": 7,
        "shadow": 4,
        "caption_margin_v": 340,
    },
    "friendly": {
        "font": "Avenir Next",
        "font_size": 88,
        "primary_color": "FFF6E5",
        "highlight_color": "4DD4AC",
        "outline": 5,
        "shadow": 3,
        "caption_margin_v": 350,
    },
    "sunset": {
        "font": "Arial Narrow",
        "font_size": 94,
        "primary_color": "FFFFFF",
        "highlight_color": "FF7A45",
        "outline": 6,
        "shadow": 3,
        "caption_margin_v": 330,
    },
    "mono": {
        "font": "Courier New",
        "font_size": 84,
        "primary_color": "F5F3FF",
        "highlight_color": "8B5CF6",
        "outline": 4,
        "shadow": 2,
        "caption_margin_v": 340,
    },
    "editorial": {
        "font": "Georgia",
        "font_size": 86,
        "primary_color": "FFFDF5",
        "highlight_color": "FFD60A",
        "outline": 5,
        "shadow": 3,
        "caption_margin_v": 350,
    },
    "lyric_block": {
        "font": "Avenir Next Condensed",
        "font_size": 100,
        "italic": True,
        "primary_color": "F7F3EE",
        "highlight_color": "00E5FF",
        "outline": 5,
        "shadow": 3,
        "caption_margin_v": 340,
    },
    "velvet_serif": {
        "font": "Bodoni 72",
        "font_size": 92,
        "bold": False,
        "primary_color": "FFF8F0",
        "highlight_color": "FF3654",
        "outline": 3,
        "shadow": 2,
        "caption_margin_v": 355,
    },
    "din_condensed": {
        "font": "DIN Condensed",
        "font_size": 100,
        "primary_color": "F7F3EE",
        "highlight_color": "A8C7E8",
        "outline": 5,
        "shadow": 3,
        "caption_margin_v": 340,
    },
    "baskerville": {
        "font": "Baskerville",
        "font_size": 92,
        "bold": False,
        "primary_color": "FFF8F0",
        "highlight_color": "00A7A7",
        "outline": 3,
        "shadow": 2,
        "caption_margin_v": 355,
    },
}

# The three header treatments are quick presets that fill in the header
# controls. Each reproduces the libass look it had before RiceSuite #65: plain
# white text with a 2 px black edge; white text on a 75% black plate; and white
# text with a black edge on an opaque white plate. Plates are square (libass
# drew square boxes) with 16 px padding.
_HEADER_PRESETS: dict[str, dict[str, object]] = {
    "plain": {},
    "black_plate": {
        "header_outline": 0,
        "header_plate": "translucent",
        "header_plate_color": "000000",
        "header_plate_opacity": 75,
    },
    "white_plate": {
        "header_plate": "solid",
        "header_plate_color": "FFFFFF",
    },
}

# ``HeaderLook`` (the request model) field -> ``StyleConfig`` field.
HEADER_LOOK_FIELDS = {
    "font": "header_font",
    "size": "header_font_size",
    "color": "header_color",
    "outline": "header_outline",
    "outline_color": "header_outline_color",
    "shadow": "header_shadow",
    "plate": "header_plate",
    "plate_color": "header_plate_color",
    "plate_opacity": "header_plate_opacity",
    "plate_radius": "header_plate_radius",
    "plate_padding": "header_padding",
    "align": "header_align",
    "line_spacing": "header_line_spacing",
    "y": "header_margin_v",
}


def header_preset(name: str) -> dict[str, object]:
    """The header controls a preset fills in, keyed by ``HeaderLook`` field."""
    style = replace(StyleConfig(), **_HEADER_PRESETS.get(name, {}))
    return {look: getattr(style, field) for look, field in HEADER_LOOK_FIELDS.items()}


def apply_header_look(style: StyleConfig, look) -> StyleConfig:
    """Return ``style`` with its header fields taken from ``look``.

    ``look`` is a ``HeaderLook`` (or any object with its attributes).
    """
    values = {field: getattr(look, name) for name, field in HEADER_LOOK_FIELDS.items()}
    return replace(style, **values)


def style_for_presets(
    caption_style: str = "classic", header_style: str = "plain"
) -> StyleConfig:
    """Return a defensive StyleConfig for the named built-in choices."""
    caption_values = _CAPTION_PRESETS.get(caption_style, _CAPTION_PRESETS["classic"])
    header_values = _HEADER_PRESETS.get(header_style, _HEADER_PRESETS["plain"])
    return replace(StyleConfig(), **caption_values, **header_values)


# --- colour + text helpers ---------------------------------------------------


def _style_color(rrggbb: str, alpha: int = 0) -> str:
    """Return an ASS style colour (&HAABBGGRR)."""
    rr, gg, bb = rrggbb[0:2], rrggbb[2:4], rrggbb[4:6]
    return f"&H{alpha:02X}{bb}{gg}{rr}"


def _inline_color(rrggbb: str) -> str:
    """Return an inline ``\\c`` colour override (&HBBGGRR&)."""
    rr, gg, bb = rrggbb[0:2], rrggbb[2:4], rrggbb[4:6]
    return f"&H{bb}{gg}{rr}&"


def _escape(text: str) -> str:
    """Escape text for an ASS Dialogue field."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\r\n", "\\N")
        .replace("\n", "\\N")
        .replace("\r", "\\N")
    )


def _ass_time(seconds: float) -> str:
    """Format seconds as ASS time H:MM:SS.cc (centiseconds)."""
    seconds = max(0.0, seconds)
    cs = round(seconds * 100)
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h:d}:{m:02d}:{s:02d}.{cs:02d}"


# --- event builders ----------------------------------------------------------


def _phrase_events(phrases: Sequence[Phrase], style: StyleConfig) -> list[str]:
    hi = _inline_color(style.highlight_color)
    events: list[str] = []
    for phrase in phrases:
        ws = phrase.words
        n = len(ws)
        for i, word in enumerate(ws):
            start = word.start
            # Keep the phrase on screen continuously; the highlight advances
            # word-by-word. The final word holds until its own end.
            end = ws[i + 1].start if i + 1 < n else max(word.end, phrase.end)
            if end <= start:
                end = start + 0.01
            parts = []
            for j, w in enumerate(ws):
                token = _escape(w.text.strip())
                if j == i:
                    parts.append("{\\c" + hi + "}" + token + "{\\r}")
                else:
                    parts.append(token)
            text = " ".join(parts)
            events.append(
                f"Dialogue: 0,{_ass_time(start)},{_ass_time(end)},Caption,,0,0,0,,{text}"
            )
    return events


_ALIGN_TOP = {"left": 7, "center": 8, "right": 9}


def fallback_span(header: str, style: StyleConfig) -> tuple[int, int]:
    """Estimated outer bounds of the fallback text, outline, and plate."""
    top = _fallback_margin_v(header, style)
    edge = _fallback_edge(style)
    return top - edge, top + _fallback_lines(
        header, style
    ) * style.header_font_size + edge


def _fallback_edge(style: StyleConfig) -> int:
    return max(
        style.header_outline,
        style.header_padding if style.header_plate != "none" else 0,
    )


def _fallback_lines(header: str, style: StyleConfig) -> int:
    size = style.header_font_size
    per_line = max(1, int((style.play_res_x - 160) / (size * 0.5)))
    return sum(max(1, -(-len(part) // per_line)) for part in header.split("\n"))


def _fallback_margin_v(header: str, style: StyleConfig) -> int:
    """The text's top, with the full outline/plate above the caption zone.

    libass wraps the text itself, so the height is estimated: the explicit
    lines, each wrapped at about half an em per character over the 920 px
    between the side margins.
    """
    lines = _fallback_lines(header, style)
    caption_top = style.play_res_y - CAPTION_ZONE_PX
    edge = _fallback_edge(style)
    return max(
        edge,
        min(style.header_margin_v, caption_top - lines * style.header_font_size - edge),
    )


def _fallback_header(
    header: str, duration: float, style: StyleConfig, family: str
) -> tuple[list[str], list[str]]:
    """A minimal libass header: text, edge, and plate, at the header position.

    No rounded corners or shadow; a translucent plate keeps its opacity.
    """
    x = {"left": 80, "center": style.play_res_x // 2, "right": style.play_res_x - 80}[
        style.header_align
    ]
    # A shared explicit position disables libass collision handling. Otherwise
    # captions on the plate's layer can move the box away from its text.
    text = f"{{\\pos({x},{_fallback_margin_v(header, style)})}}{_escape(header)}"
    style_line = (
        f"Style: HeaderFallback,{family},{style.header_font_size},"
        f"{_style_color(style.header_color)},{_style_color(style.header_color)},"
        f"{_style_color(style.header_outline_color)},{_style_color('000000', 255)},"
        f"-1,0,0,0,100,100,0,0,1,{style.header_outline},0,"
        f"{_ALIGN_TOP.get(style.header_align, 8)},80,80,"
        f"{_fallback_margin_v(header, style)},1"
    )
    events = [
        f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},"
        f"HeaderFallback,,0,0,0,,{text}"
    ]
    lines = [style_line]
    if style.header_plate != "none":
        # ASS BorderStyle 3 draws its box in OutlineColour, so the plate is a
        # layer of its own under the text: a box with transparent text.
        opacity = 100 if style.header_plate == "solid" else style.header_plate_opacity
        box = _style_color(style.header_plate_color, 255 - round(opacity * 2.55))
        clear = _style_color(style.header_color, 255)
        lines.insert(
            0,
            f"Style: HeaderFallbackPlate,{family},{style.header_font_size},"
            f"{clear},{clear},{box},{box},-1,0,0,0,100,100,0,0,"
            f"3,{style.header_padding},0,{_ALIGN_TOP.get(style.header_align, 8)},"
            f"80,80,{_fallback_margin_v(header, style)},1",
        )
        events.insert(
            0,
            f"Dialogue: 0,{_ass_time(0)},{_ass_time(duration)},"
            f"HeaderFallbackPlate,,0,0,0,,{text}",
        )
    return lines, events


# --- top-level ---------------------------------------------------------------


def build_ass(
    words: Sequence[WordLike],
    *,
    captions_on: bool = True,
    duration: float,
    style: StyleConfig | None = None,
    fallback_header: str = "",
    fallback_family: str = "Arial",
) -> str:
    """Render the complete ASS script for one clip.

    ``fallback_header`` is set only when the Pillow header failed; it adds the
    minimal libass header in ``fallback_family``.
    """
    style = style or StyleConfig()

    caption_style = (
        f"Style: Caption,{style.font},{style.font_size},"
        f"{_style_color(style.primary_color)},{_style_color(style.highlight_color)},"
        f"{_style_color(style.outline_color)},{_style_color('000000')},"
        f"{-1 if style.bold else 0},{-1 if style.italic else 0},0,0,100,100,0,0,"
        f"1,{style.outline},{style.shadow},2,60,60,{style.caption_margin_v},1"
    )
    header_style_lines: list[str] = []
    header_events: list[str] = []
    if fallback_header.strip():
        header_style_lines, header_events = _fallback_header(
            fallback_header.strip(), duration, style, fallback_family
        )

    phrases = group_words(words) if captions_on else []
    events = _phrase_events(phrases, style) + header_events

    lines = [
        "[Script Info]",
        "; Generated by RiceClipper",
        "ScriptType: v4.00+",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        f"PlayResX: {style.play_res_x}",
        f"PlayResY: {style.play_res_y}",
        "",
        "[V4+ Styles]",
        (
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding"
        ),
        caption_style,
        *header_style_lines,
        "",
        "[Events]",
        (
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, "
            "MarginV, Effect, Text"
        ),
        *events,
        "",
    ]
    return "\n".join(lines)
