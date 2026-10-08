"""Build an ASS subtitle script for RiceClipper.

Emits the caption layer described in SPEC.md §4-5:

* **Captions** (§5, Tier-1) — ~4-5 word phrase blocks with a per-word colour
  highlight synced to the word timestamps. Implemented as one Dialogue event per
  active-word window; the whole phrase stays on screen while the highlight walks
  across it. Entirely native to libass (no scripting/second engine).
* **Motion** (§5, RiceSuite #66) — with ``StyleConfig.motion`` on, each phrase
  pops in, the active word is scaled up, and a soft shadow layer sits under
  crisp text; the phrase is broken into lines here (``caption_lines``).

The header (§6) is drawn by Pillow (``render.header_image``) and overlaid by
ffmpeg (RiceSuite #65). This script carries a header only as the fallback when
that PNG render fails: a minimal header in the chosen font, size, colour,
outline, plate, alignment, and position, with square corners and no shadow.

The module is side-effect free, so ASS generation is unit-testable without
ffmpeg. Text is measured by a caller-supplied ``measure`` function; only the
pure line-wrapping helper comes from ``render.text_image``. ``StyleConfig`` is the parameterised template: the
Wave-2 "caption style/position config" (SPEC.md §7, D11) is filling these
variables from the UI, not rebuilding this file.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from itertools import pairwise

from render.text_image import wrap
from transcribe.phrasing import Phrase, WordLike, group_words

# The bottom band of the 1920 px frame kept for captions (render.framing
# re-exports it). The header is kept above it (RiceSuite #65).
CAPTION_ZONE_PX = 540
# A clip that shows caption emoji rows (RiceSuite #66) keeps this band
# instead: a two-line phrase lifted by one row, with its row above, reaches
# 756 px up for the 100 px presets. A row that would go higher goes below its
# phrase (a test checks every preset with two and three lines).
EMOJI_CAPTION_ZONE_PX = 760


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
    # Phrase pop, active-word bump, and soft shadow (RiceSuite #66). Off keeps
    # the caption events exactly as they were before #66.
    motion: bool = False

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
    # The caption band the header stays above: EMOJI_CAPTION_ZONE_PX for a
    # clip that shows emoji rows (RiceSuite #66).
    caption_zone: int = CAPTION_ZONE_PX


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
    "montserrat",
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
    # The bundled open-licence heavy font (RiceSuite #66), committed under
    # render/fonts/ and passed to libass with ``fontsdir``. The Fontname is the
    # face's full name; libass does not match the bare family "Montserrat".
    "montserrat": {
        "font": "Montserrat Black",
        "font_size": 100,
        "primary_color": "FFFFFF",
        "highlight_color": "FFD60A",
        "outline": 6,
        "shadow": 3,
        "caption_margin_v": 340,
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


# --- motion (RiceSuite #66) --------------------------------------------------

# The phrase pop: (ms after the phrase starts, scale %). It plays once per
# phrase; an event that starts while it runs continues it on the same clock.
POP_KEYFRAMES = ((0, 70), (80, 112), (160, 100))
# The highlighted word's scale, in %.
ACTIVE_SCALE = 110
# Caption side margins (the Caption style's MarginL / MarginR).
CAPTION_SIDE_MARGIN = 60
# Pillow measures without libass's shaping; lines keep this much spare width.
LINE_SAFETY = 1.04
# The soft shadow: offset (px), blur, and opacity (ASS alpha, 00 = opaque).
SHADOW_OFFSET = (4, 8)
SHADOW_BLUR = 8
SHADOW_ALPHA = "60"
# The shadow layer's alpha tags: transparent fill and outline, visible shadow.
SHADOW_LAYER_ALPHA = f"\\1a&HFF&\\3a&HFF&\\4a&H{SHADOW_ALPHA}&"
# Width of an average glyph, as a fraction of the size, when nothing measures.
_ESTIMATE_EM = 0.62

Measure = Callable[[str], float]


def caption_lines(
    tokens: Sequence[str], style: StyleConfig, measure: Measure
) -> tuple[list[list[int]], float]:
    """Break a phrase into lines the way libass would, but up front.

    Returns the token indices of each line and the phrase scale (1.0, or less
    when one word alone is wider than the frame). Lines keep room for the
    outline, the active-word bump, and Pillow's measuring error, so libass
    never has to wrap them (``\\q2``); an animated scale would re-wrap them.
    """
    widths = [measure(t) for t in tokens]
    bump = (ACTIVE_SCALE / 100 - 1) * max(widths, default=0.0)
    usable = style.play_res_x - 2 * CAPTION_SIDE_MARGIN - 2 * style.outline
    limit = usable / LINE_SAFETY - bump
    wrapped = wrap(
        " ".join(str(i) for i in range(len(tokens))),
        lambda key: (int(key), widths[int(key)]),
        measure(" "),
        limit,
    )
    lines = [[int(parts) for parts, _w in line] for line, _width in wrapped]
    widest = max((width for _line, width in wrapped), default=0.0)
    scale = min(1.0, limit / widest) if widest > 0 else 1.0
    return lines, scale


def _scale(pct: float) -> str:
    v = round(pct)
    return f"\\fscx{v}\\fscy{v}"


def _pop_tags(factor: float, offset_ms: int) -> str:
    """The pop for an event starting ``offset_ms`` into it, at ``factor``.

    The scale at ``offset_ms`` is set first, then each remaining keyframe is
    animated to with ``\\t`` on the pop's own clock.
    """
    keys = [(t - offset_ms, v * factor) for t, v in POP_KEYFRAMES]
    current = keys[-1][1]
    for (t0, v0), (t1, v1) in pairwise(keys):
        if t0 <= 0 < t1:
            current = v0 + (v1 - v0) * (0 - t0) / (t1 - t0)
            break
    tags = [_scale(current)]
    prev = 0
    for t, v in keys:
        if t > 0:
            tags.append(f"\\t({prev},{t},{_scale(v)})")
            prev = t
    return "".join(tags)


def _motion_scale(factor: float, offset_ms: int | None) -> str:
    """Scale tags for ``factor``: the pop when ``offset_ms`` is set, else fixed."""
    if offset_ms is not None:
        return _pop_tags(factor, offset_ms)
    return _scale(100 * factor)


def _motion_text(
    tokens: Sequence[str],
    lines: Sequence[Sequence[int]],
    active: int,
    phrase_scale: float,
    pop_offset: int | None,
    colours: tuple[str, str] | None,
) -> str:
    """One event's words, broken into ``lines``, with ``active`` scaled up.

    ``colours`` is (highlight, base) for the text layer and None for the
    shadow layer, which carries the scale motion but no colour.
    """
    line_of = {i: n for n, line in enumerate(lines) for i in line}
    on = off = ""
    if colours is not None:
        on, off = "\\c" + colours[0], "\\c" + colours[1]
    bump = _motion_scale(phrase_scale * ACTIVE_SCALE / 100, pop_offset)
    rest = _motion_scale(phrase_scale, pop_offset)
    # The bump makes the active word's line taller, which would shift the
    # other lines of a centred block each time the highlight changes line.
    # An invisible, 1% wide strut at the bump's height ends every other line,
    # so all lines keep one height (cold review S4, RiceSuite #66).
    shown = SHADOW_LAYER_ALPHA if colours is None else "\\alpha&H00&"
    strut = (
        "{\\alpha&HFF&"
        + re.sub(r"\\fscx[\d.]+", r"\\fscx1", bump)
        + "}x{"
        + shown
        + rest
        + "}"
    )
    out = ""
    for j, token in enumerate(tokens):
        if j:
            if line_of[j] != line_of[j - 1]:
                if line_of[active] != line_of[j - 1]:
                    out += strut
                out += "\\N"
            else:
                out += " "
        if j != active:
            out += token
        else:
            out += "{" + on + bump + "}" + token + "{" + off + rest + "}"
    if len(lines) > 1 and line_of[active] != line_of[len(tokens) - 1]:
        out += strut
    return out


# --- layout: line breaks, position, and emoji rows (RiceSuite #66) ----------

# A caption emoji row is this fraction of the caption size tall, this many px
# from the text block's line boxes. The gap clears the pop's 112% overshoot
# of a two-line block (about 12 px at size 100) on rendered frames.
EMOJI_ROW_EM = 0.9
EMOJI_GAP = 18


def emoji_row_height(style: StyleConfig) -> int:
    return round(style.font_size * EMOJI_ROW_EM)


def emoji_gap_below(style: StyleConfig) -> int:
    """The gap under the text for a row below it.

    Descenders and the outline reach the bottom of the last line box, and the
    pop's 112% overshoot pushes the ink about 0.12 of the size further down,
    so a row below sits further off than a row above (whose side holds the
    line box's empty ascent). On rendered frames this leaves at least 8 px at
    the pop's peak (cold review S5, RiceSuite #66).
    """
    return EMOJI_GAP + style.outline + round(0.2 * style.font_size)


def emoji_lift(style: StyleConfig) -> int:
    """How far the captions rise for the whole clip when it shows emoji rows.

    One row: a row below the text then ends near where the text did, clear of the
    platforms' bottom interface, and the text never moves between phrases.
    """
    return emoji_row_height(style) + EMOJI_GAP


@dataclass
class PhraseLayout:
    """Where one phrase is drawn, and its emoji row if it has one.

    ``lines`` holds indices into ``phrase.words``. ``top`` and ``bottom`` are
    the block's line boxes in output px (each line is ``font_size * scale``
    tall). ``anchor`` is the global index of the word whose pick the phrase
    shows; ``row`` is "above" or "below" and ``row_box`` its (top, bottom).
    """

    phrase: Phrase
    lines: list[list[int]]
    scale: float
    top: int
    bottom: int
    center_y: int
    anchor: int | None = None
    emoji: tuple[str, ...] = ()
    row: str | None = None
    row_box: tuple[int, int] | None = None

    @property
    def start(self) -> float:
        return self.phrase.start

    @property
    def end(self) -> float:
        """When the phrase leaves the screen (its last caption event's end)."""
        last = self.phrase.words[-1]
        return max(last.end, self.phrase.end, last.start + 0.01)


def layout_phrases(
    words: Sequence[WordLike],
    style: StyleConfig,
    measure: Measure | None = None,
    emoji: Mapping[int, Sequence[str]] | None = None,
) -> list[PhraseLayout]:
    """Lay out every caption phrase, with its emoji row if it has a pick.

    ``emoji`` maps a global word index (the word's position in ``words``) to
    its emoji. A phrase shows the pick of the first anchor it contains; a pick
    on a word that is emptied, or past the end, shows nowhere. When any phrase
    shows a row, every phrase is lifted by :func:`emoji_lift`. A row goes
    above the text when its anchor is on the first line (so always for a
    one-line phrase) and below it otherwise.
    """
    if measure is None:
        size = style.font_size
        measure = lambda text: len(text) * size * _ESTIMATE_EM  # noqa: E731
    picks = dict(emoji or {})
    index_of = {id(w): i for i, w in enumerate(words)}
    phrases = group_words(words)
    anchors = []
    for phrase in phrases:
        indices = [index_of[id(w)] for w in phrase.words]
        anchors.append(next((i for i in indices if i in picks), None))
    lift = emoji_lift(style) if any(a is not None for a in anchors) else 0
    bottom = style.play_res_y - style.caption_margin_v - lift
    row_h = emoji_row_height(style)
    layouts = []
    for phrase, anchor in zip(phrases, anchors, strict=True):
        lines, scale = caption_lines(
            [w.text.strip() for w in phrase.words], style, measure
        )
        height = len(lines) * style.font_size * scale
        layout = PhraseLayout(
            phrase=phrase,
            lines=lines,
            scale=scale,
            top=round(bottom - height),
            bottom=bottom,
            center_y=round(bottom - height / 2),
        )
        if anchor is not None:
            local = [index_of[id(w)] for w in phrase.words].index(anchor)
            # Above for a first-line anchor, unless the row would leave the
            # emoji caption zone (a phrase of three or more lines).
            zone_top = style.play_res_y - EMOJI_CAPTION_ZONE_PX
            above = local in lines[0] and layout.top - EMOJI_GAP - row_h >= zone_top
            layout.anchor = anchor
            layout.emoji = tuple(picks[anchor])
            layout.row = "above" if above else "below"
            if above:
                row_bottom = layout.top - EMOJI_GAP
                layout.row_box = (row_bottom - row_h, row_bottom)
            else:
                below = bottom + emoji_gap_below(style)
                layout.row_box = (below, below + row_h)
        layouts.append(layout)
    return layouts


def _layout_events(layouts: Sequence[PhraseLayout], style: StyleConfig) -> list[str]:
    """Caption events at the laid-out positions, with explicit line breaks.

    With Motion, each active-word window is two events: a shadow layer
    (transparent text whose blurred, offset shadow shows) under a crisp text
    layer with no shadow, and the phrase pops and the active word grows.
    Without it (a clip with emoji rows), one layer keeps the flat look.
    Colour and scale are restored after the active word explicitly; ``\\r``
    would drop the pop and the line's other tags.
    """
    colours = (
        _inline_color(style.highlight_color),
        _inline_color(style.primary_color),
    )
    pop_end = POP_KEYFRAMES[-1][0]
    dx, dy = SHADOW_OFFSET
    x = style.play_res_x // 2
    events: list[str] = []
    for layout in layouts:
        ws = layout.phrase.words
        tokens = [_escape(w.text.strip()) for w in ws]
        lines, phrase_scale = layout.lines, layout.scale
        lead = f"\\an5\\pos({x},{layout.center_y})\\q2"
        phrase_cs = round(layout.phrase.start * 100)
        n = len(ws)
        for i, word in enumerate(ws):
            start = word.start
            end = ws[i + 1].start if i + 1 < n else max(word.end, layout.phrase.end)
            if end <= start:
                end = start + 0.01
            span = f"{_ass_time(start)},{_ass_time(end)}"
            if not style.motion:
                scale = _scale(100 * phrase_scale) if phrase_scale != 1.0 else ""
                text = _flat_text(tokens, lines, i, colours)
                events.append(
                    f"Dialogue: 0,{span},Caption,,0,0,0,,{{{lead}{scale}}}{text}"
                )
                continue
            offset_ms = (round(start * 100) - phrase_cs) * 10
            pop = offset_ms if offset_ms < pop_end else None
            lead_scale = ""
            if pop is not None or phrase_scale != 1.0:
                lead_scale = _motion_scale(phrase_scale, pop)
            shadow = (
                f"{{{lead}{SHADOW_LAYER_ALPHA}"
                f"\\xshad{dx}\\yshad{dy}\\blur{SHADOW_BLUR}{lead_scale}}}"
                + _motion_text(tokens, lines, i, phrase_scale, pop, None)
            )
            crisp = f"{{{lead}\\shad0{lead_scale}}}" + _motion_text(
                tokens, lines, i, phrase_scale, pop, colours
            )
            events.append(f"Dialogue: 0,{span},Caption,,0,0,0,,{shadow}")
            events.append(f"Dialogue: 1,{span},Caption,,0,0,0,,{crisp}")
    return events


def _flat_text(
    tokens: Sequence[str],
    lines: Sequence[Sequence[int]],
    active: int,
    colours: tuple[str, str],
) -> str:
    """One event's words, broken into ``lines``, with the highlight only."""
    line_of = {i: n for n, line in enumerate(lines) for i in line}
    out = ""
    for j, token in enumerate(tokens):
        if j:
            out += "\\N" if line_of[j] != line_of[j - 1] else " "
        if j == active:
            out += "{\\c" + colours[0] + "}" + token + "{\\c" + colours[1] + "}"
        else:
            out += token
    return out


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
    caption_top = style.play_res_y - style.caption_zone
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
    measure: Measure | None = None,
    emoji: Mapping[int, Sequence[str]] | None = None,
) -> str:
    """Render the complete ASS script for one clip.

    ``fallback_header`` is set only when the Pillow header failed; it adds the
    minimal libass header in ``fallback_family``. ``measure(text) -> px`` sizes
    caption text for the line breaks Motion makes (``caption_lines``); without
    it, widths are estimated from the character count. ``emoji`` maps global
    word indices to picks (``layout_phrases``); when a pick shows, every
    caption is lifted to make room for the rows.
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

    caption_events: list[str] = []
    if captions_on:
        layouts = layout_phrases(words, style, measure, emoji)
        if style.motion or any(p.anchor is not None for p in layouts):
            caption_events = _layout_events(layouts, style)
        else:
            caption_events = _phrase_events([p.phrase for p in layouts], style)
    events = caption_events + header_events

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
