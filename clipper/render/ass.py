"""Build an ASS subtitle script for RiceClipper.

Emits a single ASS file carrying BOTH layers described in SPEC.md §4-6:

* **Captions** (§5, Tier-1) — ~4-5 word phrase blocks with a per-word colour
  highlight synced to the word timestamps. Implemented as one Dialogue event per
  active-word window; the whole phrase stays on screen while the highlight walks
  across it. Entirely native to libass (no scripting/second engine).
* **Header** (§6.1) — the manual 1-2 line hook pinned to the top, using one of
  the small built-in header treatments, present for the full clip and cleared
  above the caption zone.

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
    primary_color: str = "FFFFFF"  # base word colour, RRGGBB
    highlight_color: str = "35E36B"  # active word colour, RRGGBB
    outline_color: str = "000000"
    outline: int = 6  # thick outline for legibility on any bg
    shadow: int = 3
    caption_margin_v: int = 340  # px up from the bottom (lower third)

    # Header (top text or optional plate).
    header_font: str = "Arial"
    header_font_size: int = 42
    header_color: str = "FFFFFF"
    header_outline_color: str = "000000"
    header_plate_color: str = "000000"
    header_plate_alpha: int = 255  # ASS alpha: 0 opaque .. 255 transparent
    header_padding: int = 0  # opaque-box padding around the text
    header_border_style: int = 1  # 1 = outline, 3 = opaque box
    header_outline: int = 2
    header_shadow: int = 2
    header_margin_v: int = 450  # px down from the top (~23% of 1920px)


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

_HEADER_PRESETS: dict[str, dict[str, object]] = {
    # Reference-matched default: compact white text with a black edge and no
    # plate. The same 42px scale is used by all three header choices.
    "plain": {
        "header_color": "FFFFFF",
        "header_outline_color": "000000",
        "header_plate_color": "000000",
        "header_plate_alpha": 255,
        "header_padding": 0,
        "header_border_style": 1,
        "header_outline": 2,
        "header_shadow": 2,
    },
    "black_plate": {
        "header_color": "FFFFFF",
        "header_outline_color": "000000",
        "header_plate_color": "000000",
        "header_plate_alpha": 65,
        "header_padding": 16,
        "header_border_style": 3,
        "header_outline": 2,
        "header_shadow": 0,
    },
    "white_plate": {
        "header_color": "FFFFFF",
        "header_outline_color": "000000",
        "header_plate_color": "FFFFFF",
        "header_plate_alpha": 0,
        "header_padding": 16,
        "header_border_style": 3,
        "header_outline": 2,
        "header_shadow": 0,
    },
}


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


def _header_event(
    header: str,
    duration: float,
    style: StyleConfig,
    *,
    style_name: str = "Header",
    layer: int = 1,
) -> list[str]:
    header = header.strip()
    if not header:
        return []
    text = _escape(header)
    return [
        f"Dialogue: {layer},{_ass_time(0)},{_ass_time(duration)},"
        f"{style_name},,0,0,0,,{text}"
    ]


# --- top-level ---------------------------------------------------------------


def build_ass(
    words: Sequence[WordLike],
    *,
    header: str = "",
    captions_on: bool = True,
    duration: float,
    style: StyleConfig | None = None,
) -> str:
    """Render the complete ASS script for one clip."""
    style = style or StyleConfig()

    caption_style = (
        f"Style: Caption,{style.font},{style.font_size},"
        f"{_style_color(style.primary_color)},{_style_color(style.highlight_color)},"
        f"{_style_color(style.outline_color)},{_style_color('000000')},"
        f"{-1 if style.bold else 0},0,0,0,100,100,0,0,"
        f"1,{style.outline},{style.shadow},2,60,60,{style.caption_margin_v},1"
    )
    # BorderStyle 3 = opaque box; BorderStyle 1 = plain text with an outline.
    header_box_color = _style_color(style.header_plate_color, style.header_plate_alpha)
    header_outline_color = (
        header_box_color
        if style.header_border_style == 3
        else _style_color(style.header_outline_color)
    )
    header_outline = (
        style.header_padding if style.header_border_style == 3 else style.header_outline
    )
    header_style = (
        f"Style: Header,{style.header_font},{style.header_font_size},"
        f"{_style_color(style.header_color)},{_style_color(style.header_color)},"
        f"{header_outline_color},{header_box_color},"
        f"-1,0,0,0,100,100,0,0,"
        f"{style.header_border_style},{header_outline},{style.header_shadow},"
        f"8,80,80,{style.header_margin_v},1"
    )
    header_style_lines = [header_style]
    header_events = _header_event(header, duration, style)

    # ASS BorderStyle 3 uses OutlineColour for the box itself, so it cannot
    # independently express a white plate plus a black text outline. The
    # white-plate preset therefore gets two aligned layers: an opaque white
    # box with transparent text, followed by white text with a black outline.
    if (
        style.header_border_style == 3
        and style.header_plate_color == "FFFFFF"
        and style.header_color == "FFFFFF"
    ):
        transparent = _style_color(style.header_color, 255)
        header_plate_style = (
            f"Style: HeaderPlate,{style.header_font},{style.header_font_size},"
            f"{transparent},{transparent},{header_box_color},{header_box_color},"
            f"-1,0,0,0,100,100,0,0,3,{style.header_padding},"
            f"{style.header_shadow},8,80,80,{style.header_margin_v},1"
        )
        header_text_style = (
            f"Style: Header,{style.header_font},{style.header_font_size},"
            f"{_style_color(style.header_color)},{_style_color(style.header_color)},"
            f"{_style_color(style.header_outline_color)},"
            f"{_style_color('000000', 255)},"
            f"-1,0,0,0,100,100,0,0,1,{style.header_outline},"
            f"{style.header_shadow},8,80,80,{style.header_margin_v},1"
        )
        header_style_lines = [header_plate_style, header_text_style]
        header_events = _header_event(
            header, duration, style, style_name="HeaderPlate", layer=0
        ) + _header_event(header, duration, style, style_name="Header", layer=1)

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
