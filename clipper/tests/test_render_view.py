"""Issue #20 (variant C): the rendered clip is a 9:16 column in the review grid.

``scripts/check_editor_browser.py`` measures the live layout in Chrome at every
ratified viewport; these contracts run in CI, which has no browser. They pin
the structure the layout depends on and tie the empty frame's guide bands to
the render constants, so moving the header moves the guides.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from render.ass import StyleConfig
from render.framing import CAPTION_ZONE_PX, HEADER_BLOCK_MAX_PX

ROOT = Path(__file__).resolve().parents[1]


def _css() -> str:
    return " ".join((ROOT / "web/style.css").read_text(encoding="utf-8").split())


def _rules(css: str) -> list[tuple[str | None, list[str], list[tuple[str, str]]]]:
    """Parse flat CSS into (media, selectors, declarations) in source order."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules, media, start = [], None, 0
    i = 0
    while i < len(css):
        if css[i] == "{":
            head = css[start:i].strip()
            if head.startswith("@media"):
                media, start = head, i + 1
            else:
                end = css.index("}", i)
                declarations = [
                    tuple(part.strip() for part in d.split(":", 1))
                    for d in css[i + 1 : end].split(";")
                    if ":" in d
                ]
                selectors = [x.strip() for x in head.split(",")]
                rules.append((media, selectors, declarations))
                i, start = end, end + 1
        elif css[i] == "}":
            media, start = None, i + 1
        i += 1
    return rules


def _matches(selector: str, element: str, states: set, ancestors: set) -> bool:
    """Whether ``selector`` can style ``.element`` in ``states`` under ``ancestors``."""
    # ``:not(...)`` only narrows a match, so it is ignored: conservative for
    # finding rules that could override.
    compounds = re.sub(r":not\([^)]*\)", "", selector).replace(">", " ").split()
    own = set(re.findall(r"\.([\w-]+)", compounds[-1]))
    if element not in own or not own <= {element} | states:
        return False
    return all(set(re.findall(r"\.([\w-]+)", c)) <= ancestors for c in compounds[:-1])


def _effective(
    prop: str,
    element: str,
    *,
    states: frozenset = frozenset(),
    ancestors: frozenset = frozenset({"clip-card", "review-grid", "clip-result"}),
    narrow: bool = False,
    css: str | None = None,
) -> str | None:
    """The last declared value of ``prop`` that can reach the element.

    Specificity is ignored on purpose: any later rule that can reach the
    element, in any state or ancestor context given, counts as an override.
    """
    value = None
    for media, selectors, declarations in _rules(css if css is not None else _css()):
        if media is not None and not (narrow and "max-width: 880px" in media):
            continue
        if any(_matches(x, element, set(states), set(ancestors)) for x in selectors):
            for name, val in declarations:
                if name == prop:
                    value = val
    return value


RESULT_STATES = [
    frozenset(),
    frozenset({"is-empty"}),
    frozenset({"is-busy"}),
    frozenset({"is-stale"}),
    frozenset({"is-empty", "is-busy"}),
]


def _classes(attrs) -> list[str]:
    return (dict(attrs).get("class") or "").split()


class _Tree(HTMLParser):
    """Record the class path of every element inside the clip-card template."""

    VOID = frozenset({"input", "img", "br", "source", "meta", "link"})

    def __init__(self):
        super().__init__()
        self.stack: list[tuple[str, list[str]]] = []
        self.found: list[tuple[list[str], list[str], dict]] = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        classes = _classes(attrs)
        path = [" ".join(c) for _, c in self.stack if c]
        self.found.append((path, classes, attributes))
        if tag not in self.VOID:
            self.stack.append((tag, classes))

    def handle_startendtag(self, tag, attrs):
        # ``<br />`` and ``<input />`` open nothing, so nothing is closed.
        self.found.append(
            ([" ".join(c) for _, c in self.stack if c], _classes(attrs), dict(attrs))
        )

    def handle_endtag(self, tag):
        while self.stack:
            if self.stack.pop()[0] == tag:
                break


def _elements():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    template = html.split('<template id="clip-card-template">', 1)[1]
    tree = _Tree()
    tree.feed(template.split("</template>", 1)[0])
    return tree.found


def _find(name: str):
    matches = [(p, c, a) for p, c, a in _elements() if name in c]
    assert len(matches) == 1, f"expected one .{name}, got {len(matches)}"
    return matches[0]


def test_rendered_column_sits_in_the_review_grid_after_the_controls():
    grid_children = [
        classes[0]
        for path, classes, _ in _elements()
        if classes and path and path[-1] == "review-grid"
    ]
    assert grid_children == ["preview-col", "edit-col", "clip-result", "text-row"]


def test_rendered_column_starts_as_an_empty_frame_not_hidden():
    _, classes, _ = _find("clip-result")
    assert "is-empty" in classes
    assert "hidden" not in classes
    path, _, attributes = _find("output-video")
    assert path[-1] == "result-frame"
    # No intrinsic size hint: the frame, not the media, sets the shape.
    assert "width" not in attributes and "height" not in attributes
    assert _find("result-empty")[0][-1] == "result-frame"
    assert _find("result-busy")[0][-1] == "result-frame"
    assert _find("download-link")[0][-1] == "clip-result is-empty"


def test_transcript_and_lyrics_share_one_row_wrapper():
    assert _find("transcript-panel")[0][-1] == "text-row"
    assert _find("lyrics")[0][-1] == "text-row"


def _assert_frame_contract(css: str) -> None:
    for narrow in (False, True):
        for states in RESULT_STATES:
            ctx = {"states": states, "narrow": narrow, "css": css}
            assert _effective("display", "clip-result", **ctx) != "none", ctx
            assert _effective("order", "clip-result", **ctx) is None, ctx
            assert _effective("aspect-ratio", "result-frame", **ctx) == "9 / 16", ctx
            video = {
                "narrow": narrow,
                "css": css,
                "ancestors": frozenset(
                    {"clip-card", "review-grid", "clip-result", "result-frame"} | states
                ),
            }
            assert _effective("object-fit", "output-video", **video) == "cover", ctx
            assert _effective("padding", "output-video", **video) == "0", ctx
            assert _effective("width", "output-video", **video) == "100%", ctx
            assert _effective("height", "output-video", **video) == "100%", ctx
    assert _effective("height", "result-frame", css=css) == (
        "clamp(360px, min(42vw, 100vh - 200px), 540px)"
    )
    assert _effective("height", "result-frame", narrow=True, css=css) == "auto"
    assert _effective("width", "result-frame", narrow=True, css=css) == (
        "min(100%, 360px)"
    )
    assert _effective("flex-direction", "review-grid", narrow=True, css=css) == (
        "column"
    )
    assert _effective("flex-direction", "text-row", narrow=True, css=css) == "column"


def test_frame_is_nine_by_sixteen_in_every_state_and_never_letterboxes():
    _assert_frame_contract(_css())


# The review of PR #39 showed the earlier string checks passing against each
# of these; the cascade-aware contract must reject every one.
BREAKING_OVERRIDES = [
    ".output-video { object-fit: contain; }",
    ".clip-result.is-empty { display: none; }",
    "@media (max-width: 880px) { .clip-result { order: 99; } }",
    "@media (max-width: 880px) { .result-frame { height: 200px; } }",
    ".clip-result.is-busy .output-video { object-fit: contain; }",
    ".result-frame { aspect-ratio: 16 / 9; }",
]


@pytest.mark.parametrize("override", BREAKING_OVERRIDES)
def test_contract_rejects_a_later_breaking_override(override):
    with pytest.raises(AssertionError):
        _assert_frame_contract(_css() + " " + override)


def test_music_panes_split_the_full_row_in_equal_halves():
    assert _effective("grid-column", "text-row") == "1 / -1"
    speech = frozenset({"clip-card", "review-grid"})
    music = speech | {"music-review"}
    assert _effective("grid-template-columns", "text-row", ancestors=speech) == (
        "minmax(0, 1fr)"
    )
    assert _effective("grid-template-columns", "text-row", ancestors=music) == (
        "repeat(2, minmax(0, 1fr))"
    )
    # Both panes share the heading row, so their text boxes start level.
    panel = music | {"text-row"}
    assert _effective("grid-template-rows", "text-panel", ancestors=panel) == "subgrid"


def test_guide_bands_follow_the_render_constants():
    top = _effective("top", "guide-header")
    height = _effective("height", "guide-header")
    assert top == f"calc({StyleConfig().header_margin_v} / 1920 * 100%)"
    assert height == f"calc({HEADER_BLOCK_MAX_PX} / 1920 * 100%)"
    margin = StyleConfig().caption_margin_v
    assert _effective("bottom", "guide-caption") == f"calc({margin} / 1920 * 100%)"
    assert _effective("height", "guide-caption") == (
        f"calc(({CAPTION_ZONE_PX} - {margin}) / 1920 * 100%)"
    )


def test_stale_note_shows_only_on_a_stale_render_that_is_not_rerendering():
    shown = {
        states: _effective(
            "display",
            "result-stale",
            ancestors=frozenset(
                {"clip-card", "review-grid", "result-frame", "clip-result"} | states
            ),
        )
        for states in RESULT_STATES
    }
    assert shown[frozenset({"is-stale"})] == "flex"
    assert shown[frozenset()] == "none"
    assert _find("result-stale")[0][-1] == "result-frame"


def test_stale_note_matches_the_start_over_button():
    # RiceSuite #52: the stale note uses the destructive style of Start over.
    rules = _rules(_css())

    def declared(selector: str) -> dict[str, str]:
        found: dict[str, str] = {}
        for media, selectors, declarations in rules:
            if media is None and selector in selectors:
                found.update(declarations)
        return found

    note = {**declared(".result-stale"), **declared(".result-stale span")}
    button = {**declared("button"), **declared("#restart-btn")}
    assert note["border"] == "1px solid var(--destructive)"
    assert note["color"] == button["color"] == "var(--destructive)"
    assert note["background"] == button["background"] == "var(--backdrop)"
    assert note["border-radius"] == button["border-radius"] == "5px"
    assert "font-family" not in note
