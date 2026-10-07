"""Caption Motion (and later emoji) controls in the review UI (RiceSuite #66)."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _html() -> str:
    return (ROOT / "web/index.html").read_text(encoding="utf-8")


def _js() -> str:
    return (ROOT / "web/app.js").read_text(encoding="utf-8")


def test_caption_ui_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the caption UI behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("caption-ui.test.cjs"))],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_motion_toggle_is_offered_on_by_default_and_sent():
    html = _html()
    js = _js()
    assert '<input class="motion-toggle" type="checkbox" checked />' in html
    assert "motion: clip.motionToggleEl.checked," in js
    # Saved per slot like the caption style, and carried when slots renumber.
    assert 'slotFlag(clip.ord, "motion", true)' in js
    assert 'rememberSlotStyle(clip.ord, "motion", clip.motionToggleEl.checked)' in js
    assert 'rememberSlotStyle(ord, "motion", clip.motionToggleEl.checked)' in js
    # A photo has no captions, so no Motion either.
    css = (ROOT / "web/style.css").read_text(encoding="utf-8")
    assert ".photo-card .motion-row" in css


def test_the_bundled_font_preset_has_a_card():
    html = _html()
    assert 'value="montserrat"' in html
    assert '<span class="choice-label">Montserrat</span>' in html
