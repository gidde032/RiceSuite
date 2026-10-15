"""Composed fullscreen and decorative TikTok previews (#88, #89)."""

import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest

from tests.test_render_view import _find

ROOT = Path(__file__).resolve().parents[1]


def test_preview_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for preview behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("preview-ui.test.cjs"))],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_header_and_source_media_share_the_fullscreen_ancestor():
    for name in ("source-video", "source-photo", "header-preview-window"):
        assert _find(name)[0][-1] == "source-preview"
    for name in ("source-video", "output-video"):
        assert _find(name)[2]["controlslist"] == "nofullscreen"
    assert _find("result-frame")[0][-1] == "rendered-preview"


def test_overlay_is_a_local_static_frame_without_external_dependencies():
    svg = ElementTree.parse(ROOT / "web/tiktok-preview.svg").getroot()
    assert svg.attrib["viewBox"] == "0 0 1080 1920"
    for node in svg.iter():
        assert not node.tag.endswith(("script", "image", "foreignObject", "a"))
        assert not any("href" in key for key in node.attrib)
    text = " ".join(svg.itertext())
    for part in (
        "Following",
        "For You",
        "@your_username",
        "Original sound",
        "Home",
        "Inbox",
        "Profile",
    ):
        assert part in text
