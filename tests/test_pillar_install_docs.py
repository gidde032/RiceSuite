"""The pillar guides must install their shared suite dependency."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("pillar", ["searcher", "clipper", "poster"])
def test_pillar_install_guide_uses_suite_checkout(pillar):
    readme = (ROOT / pillar / "README.md").read_text()
    section = readme.split("## Install", 1)[1].split("\n## ", 1)[0]
    assert "github.com/gidde032/RiceSuite.git" in section
    assert "cd RiceSuite" in section
    assert "pip install -e ." in section
