"""Issue #20 (variant C): the rendered clip is a 9:16 column in the review grid.

``scripts/check_editor_browser.py`` measures the live layout in Chrome at every
ratified viewport; these contracts run in CI, which has no browser. They pin
the structure the layout depends on and tie the empty frame's guide bands to
the render constants, so moving the header moves the guides.
"""

import re
from html.parser import HTMLParser
from pathlib import Path

from render.ass import StyleConfig
from render.framing import CAPTION_ZONE_PX, HEADER_BLOCK_MAX_PX

ROOT = Path(__file__).resolve().parents[1]


def _css() -> str:
    return " ".join((ROOT / "web/style.css").read_text(encoding="utf-8").split())


def _rule(selector: str, css: str | None = None) -> str:
    css = css if css is not None else _css()
    match = re.search(re.escape(selector) + r" \{([^}]*)\}", css)
    assert match, f"no rule for {selector}"
    return match.group(1)


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


def test_frame_is_nine_by_sixteen_and_never_letterboxes():
    frame = _rule(".result-frame")
    assert "aspect-ratio: 9 / 16;" in frame
    assert "height: 540px;" in frame
    video = _rule(".output-video")
    assert "object-fit: cover;" in video
    assert "padding: 0;" in video and "border: 0;" in video
    assert "width: 100%; height: 100%;" in video


def test_music_panes_split_the_full_row_in_equal_halves():
    assert "grid-column: 1 / -1;" in _rule(".text-row")
    assert "grid-template-columns: repeat(2, minmax(0, 1fr));" in _rule(
        ".review-grid.music-review .text-row"
    )


def test_guide_bands_follow_the_render_constants():
    header = _rule(".guide-header")
    top, block = re.search(
        r"top: calc\((\d+) / 1920 \* 100%\); height: calc\((\d+) / 1920", header
    ).groups()
    assert int(top) == StyleConfig().header_margin_v
    assert int(block) == HEADER_BLOCK_MAX_PX
    caption = _rule(".guide-caption")
    bottom, zone, margin = re.search(
        r"bottom: calc\((\d+) / 1920 \* 100%\); "
        r"height: calc\(\((\d+) - (\d+)\) / 1920",
        caption,
    ).groups()
    assert int(bottom) == int(margin) == StyleConfig().caption_margin_v
    assert int(zone) == CAPTION_ZONE_PX


def test_rendered_column_stacks_below_the_existing_breakpoint():
    css = _css()
    narrow = css.split("@media (max-width: 880px) {", 1)[1].split("@media", 1)[0]
    assert "flex-direction: column;" in _rule(".review-grid", narrow)
    assert "width: min(100%, 360px); height: auto;" in _rule(".result-frame", narrow)
    assert "flex-direction: column;" in _rule(".text-row", narrow)
