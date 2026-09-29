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


def test_clipper_first_run_activates_suite_venv():
    readme = (ROOT / "clipper" / "README.md").read_text()
    section = readme.split("## First run", 1)[1].split("\n## ", 1)[0]
    assert "source ../.venv/bin/activate" in section


def test_poster_suite_install_does_not_install_standalone_hooks():
    readme = (ROOT / "poster" / "README.md").read_text()
    section = readme.split("## Installation", 1)[1].split("\n## ", 1)[0]
    code = section.split("```bash", 1)[1].split("```", 1)[0]
    assert "pre-commit install" not in code
    assert "poster/.pre-commit-config.yaml" in section
