"""Public style/CI contracts complement actual rendered browser assertions."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "searcher/ricesearcher/web/static"


def test_canonical_stylesheet_precedes_searcher_styles_on_every_page():
    for page in ("index.html", "media.html", "profiles.html"):
        html = (STATIC / page).read_text()
        sheets = re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)
        assert sheets[:2] == ["static/slate.css", "static/styles.css"]


def test_workspace_reuses_tokens_and_derived_spacing():
    css = (STATIC / "styles.css").read_text()
    workspace = css.split("/* Workspace spacing", 1)[1]
    assert "--searcher-space: 8px" in workspace
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", workspace)
    assert "var(--interior)" in workspace and "var(--control-line)" in workspace
    assert "clamp(240px, 30vw, 360px)" in workspace
    # Insets/gaps in the new surface must use the named unit, including narrow.
    for property, value in re.findall(
        r"(padding|margin|gap|column-gap)\s*:\s*([^;}]+)", workspace
    ):
        assert "--searcher-space" in value or value.strip() == "0", (property, value)


def test_required_browser_checker_cannot_silently_skip_the_matrix():
    text = (ROOT / "scripts/check_searcher_workspace_browser.py").read_text()
    assert "importorskip" not in text and "pytest.skip" not in text
    for width in (320, 390, 768, 899, 900, 1024, 1366, 1440, 1920):
        assert f"({width}, " in text
    assert "source.mp4" in text and "readyState >= 2" in text
    assert "scrollWidth <= innerWidth" in text
    assert "--searcher-space','12px'" in text
