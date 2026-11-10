"""Composed fullscreen and decorative TikTok previews (#88, #89)."""

import base64
import io
import json
import shutil
import subprocess
from pathlib import Path
from xml.etree import ElementTree

import pytest
from PIL import Image

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
        attrs = _find(name)[2]
        assert "controls" in attrs
        assert attrs["controlslist"] == "nofullscreen"
    assert _find("result-frame")[0][-1] == "rendered-preview"


def test_overlay_is_a_local_static_frame_without_external_dependencies():
    svg = ElementTree.parse(ROOT / "web/tiktok-preview.svg").getroot()
    assert svg.attrib["viewBox"] == "0 0 1080 1920"
    images = []
    for node in svg.iter():
        assert not node.tag.endswith(("script", "foreignObject", "a"))
        for key, value in node.attrib.items():
            if "href" in key:
                assert node.tag.endswith("image")
                assert value.startswith("data:image/png;base64,")
                image = Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1])))
                assert image.mode == "RGBA"
                assert image.width < 150 and image.height < 150
                assert image.getextrema()[3] == (0, 255)
                images.append(node.attrib["id"])
    assert images == ["avatar", "like", "comment", "save", "share"]
    text = " ".join(svg.itertext())
    for part in ("Following", "For You", "@your_username", "Add 1st", "Share"):
        assert part in text
    # App navigation is below the video, not an obstruction over its pixels.
    for part in ("Home", "Inbox", "Profile", "Original sound"):
        assert part not in text
    ns = {"s": "http://www.w3.org/2000/svg"}
    like = svg.find(".//s:image[@id='like']", ns)
    assert float(like.attrib["x"]) == pytest.approx(833 * 1080 / 946, abs=0.01)
    assert float(like.attrib["y"]) == pytest.approx((1070 - 166) * 1080 / 946, abs=0.01)


def test_previews_keep_browser_controls_without_a_custom_transport_bar():
    html = (ROOT / "web/index.html").read_text()
    js = (ROOT / "web/app.js").read_text()
    for name in ("preview-play", "preview-seek", "preview-mute", "preview-volume"):
        assert name not in html
    assert "media.controls = false" not in js


def test_blur_pad_zoom_slider_sits_in_the_geometry_fieldset():
    """The slider app.js wires lives under the Geometry cards (RiceSuite #98)."""
    template = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    start = template.index('<fieldset class="field choice-field geometry"')
    fieldset = template[start : template.index("</fieldset>", start)]
    cards = fieldset.index("geometry-choice-grid")
    slider = fieldset.index('<label class="vol blur-pad-zoom" hidden>')
    assert cards < slider
    assert (
        '<input class="blur-pad-zoom-input" type="range" min="100" max="100"'
        in fieldset
    )
    assert '<output class="vol-label blur-pad-zoom-out">100%</output>' in fieldset
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for selector in (".blur-pad-zoom", ".blur-pad-zoom-input", ".blur-pad-zoom-out"):
        assert f'node.querySelector("{selector}")' in script


def test_blur_pad_zoom_max_matches_the_render_in_the_browser():
    """app.js and render.geometry agree on every clip's zoom maximum."""
    import re

    from render import geometry

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the zoom parity check")
    script = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    source = re.search(r"^function blurPadZoomMax\(.*?^}\n", script, re.M | re.S)
    assert source is not None
    sizes = [
        (w, h)
        for w in (0, 640, 720, 1080, 1081, 1280, 1440, 1920, 2560, 3840, 5120)
        for h in (0, 360, 480, 720, 1080, 1440, 1920, 2160)
    ]
    sizes += [(1921, 1081), (3001, 1003), (999, 998), (4096, 1716)]
    code = source.group(0) + (
        f"console.log(JSON.stringify({json.dumps(sizes)}"
        ".map(([w, h]) => blurPadZoomMax(w, h))));"
    )
    result = subprocess.run(
        [node, "-e", code], text=True, capture_output=True, timeout=30, check=True
    )
    browser = json.loads(result.stdout)
    assert browser == [geometry.blur_pad_zoom_max(w, h) for w, h in sizes]
