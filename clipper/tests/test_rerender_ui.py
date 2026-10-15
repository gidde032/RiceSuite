"""Per-slot render and Render all for changed slots only (RiceSuite #86).

The behaviour runs in ``rerender-ui.test.cjs`` against the real ``app.js``;
these checks pin the card markup and styles it relies on.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.test_render_view import RESULT_STATES, _effective, _elements, _find

ROOT = Path(__file__).resolve().parents[1]


def test_rerender_ui_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the per-slot render UI behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("rerender-ui.test.cjs"))],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_slot_render_button_sits_under_download_in_the_rendered_column():
    path, _, attributes = _find("render-one")
    assert path[-1] == "clip-result is-empty"
    assert attributes.get("type") == "button"
    # Disabled until the clip is transcribed and ready (app.js syncRenderOne).
    assert "disabled" in attributes
    column = [
        c[0] for p, c, _ in _elements() if c and p and p[-1] == "clip-result is-empty"
    ]
    assert column == ["result-frame", "download-link", "render-one"]


def test_slot_render_button_shows_in_every_result_state_at_every_width():
    for narrow in (False, True):
        for states in RESULT_STATES:
            ctx = {"states": states, "narrow": narrow}
            assert _effective("display", "render-one", **ctx) != "none", ctx
            assert _effective("visibility", "render-one", **ctx) != "hidden", ctx
    # As wide as Download, and the narrow layout's cap matches it.
    assert _effective("width", "render-one") == _effective("width", "download-link")
    assert _effective("width", "render-one", narrow=True) == _effective(
        "width", "download-link", narrow=True
    )


def test_stale_note_says_render_all_re_renders_the_clip():
    html = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert (
        '<div class="result-stale"><span>Edited since this render'
        " — Render all will re-render it</span></div>" in html
    )
