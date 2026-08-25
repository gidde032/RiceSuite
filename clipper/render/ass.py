"""Build an ASS subtitle script for RiceClipper.

Emits a single ASS file carrying BOTH layers described in SPEC.md §4-6:

* **Captions** (§5, Tier-1) — ~4-5 word phrase blocks with a per-word colour
  highlight synced to the word timestamps. Implemented as one Dialogue event per
  active-word window; the whole phrase stays on screen while the highlight walks
  across it. Entirely native to libass (no scripting/second engine).
* **Header** (§6.1) — the manual 1-2 line hook pinned to the top on a legibility
  plate, present for the full clip, cleared above the caption zone.

The module is pure standard library and side-effect free, so ASS generation is
unit-testable without ffmpeg. ``StyleConfig`` is the parameterised template: the
Wave-2 "caption style/position config" (SPEC.md §7, D11) is filling these
variables from the UI, not rebuilding this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

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
    primary_color: str = "FFFFFF"      # base word colour, RRGGBB
    highlight_color: str = "35E36B"    # active word colour, RRGGBB
    outline_color: str = "000000"
    outline: int = 6                   # thick outline for legibility on any bg
    shadow: int = 3
    caption_margin_v: int = 340        # px up from the bottom (lower third)

    # Header (top plate).
    header_font: str = "Arial"
    header_font_size: int = 66
    header_color: str = "FFFFFF"
    header_plate_color: str = "000000"
    header_plate_alpha: int = 90       # ASS alpha: 0 opaque .. 255 transparent
    header_padding: int = 14           # opaque-box padding around the text
    header_margin_v: int = 120         # px down from the top


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
    cs = int(round(seconds * 100))
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


def _header_event(header: str, duration: float, style: StyleConfig) -> list[str]:
    header = header.strip()
    if not header:
        return []
    text = _escape(header)
    return [
        f"Dialogue: 1,{_ass_time(0)},{_ass_time(duration)},Header,,0,0,0,,{text}"
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
    # BorderStyle 3 = opaque box; OutlineColour (with alpha) is the plate.
    header_style = (
        f"Style: Header,{style.header_font},{style.header_font_size},"
        f"{_style_color(style.header_color)},{_style_color(style.header_color)},"
        f"{_style_color(style.header_plate_color, style.header_plate_alpha)},"
        f"{_style_color(style.header_plate_color, style.header_plate_alpha)},"
        f"-1,0,0,0,100,100,0,0,"
        f"3,{style.header_padding},0,8,80,80,{style.header_margin_v},1"
    )

    phrases = group_words(words) if captions_on else []

    events = _phrase_events(phrases, style) + _header_event(header, duration, style)

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
        header_style,
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
