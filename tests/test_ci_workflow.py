"""The suite CI runs every pillar's existing gates at their existing floors.

ADR-001 Q16: one CI, every pillar's current gates and coverage floor, none
weakened, no averaged floor. Clipper and Poster pin their own floors through
their gate meta-tests (which read their `clipper-*` / `poster-*` jobs here).
RiceSearcher had no such meta-test, so its gates are pinned below, along with
the suite-wide shape.
"""

import re

import pytest

from ricesuite import SUITE_ROOT

WORKFLOW = SUITE_ROOT / ".github" / "workflows" / "ci.yml"
FLOORS = {"searcher": 90, "clipper": 85, "poster": 43}


def _jobs() -> dict[str, str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    body = text.split("\njobs:\n", maxsplit=1)[1]
    blocks = re.split(r"\n(?=  [A-Za-z0-9_-]+:\n)", "\n" + body)
    jobs = {}
    for block in blocks:
        match = re.match(r"\n?  ([A-Za-z0-9_-]+):\n", block)
        if match:
            jobs[match.group(1)] = block
    return jobs


def test_only_the_suite_workflow_exists():
    """Workflows only run from the repository root; a pillar copy would be a
    gate definition that runs nowhere."""
    for pillar in FLOORS:
        assert not list((SUITE_ROOT / pillar / ".github").glob("workflows/*.y*ml"))
    assert [p.name for p in (SUITE_ROOT / ".github" / "workflows").iterdir()] == [
        "ci.yml"
    ]


def test_triggers_and_permissions():
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger = text.split("pull_request:", 1)[1].split("push:", 1)[0]
    assert "branches" not in trigger, "stacked PRs must receive checks"
    assert "contents: read" in text
    assert "pull_request_target" not in text


@pytest.mark.parametrize("pillar", sorted(FLOORS))
def test_each_pillar_runs_from_its_own_directory(pillar):
    mine = {k: v for k, v in _jobs().items() if k.startswith(f"{pillar}-")}
    assert len(mine) == 2, f"expected a 3.12 and a 3.14 job for {pillar}"
    for block in mine.values():
        assert f"working-directory: {pillar}" in block
    versions = sorted(
        re.search(r'python-version: "([\d.]+)"', b).group(1) for b in mine.values()
    )
    assert versions == ["3.12", "3.14"]


@pytest.mark.parametrize("pillar", ["clipper", "poster"])
def test_explicit_floors_match_the_pillar_floor(pillar):
    for name, block in _jobs().items():
        if name.startswith(f"{pillar}-") and "--cov-fail-under" in block:
            floors = {int(n) for n in re.findall(r"--cov-fail-under=(\d+)", block)}
            assert floors == {FLOORS[pillar]}, name


def test_no_job_lowers_a_floor_from_outside():
    """Searcher's floor lives in its pyproject addopts; nothing in CI may
    override it or switch coverage off."""
    for name, block in _jobs().items():
        assert "--no-cov" not in block, name
        assert "no:cov" not in block, name
        if name.startswith("searcher-"):
            assert "--cov-fail-under" not in block, name


def test_searcher_floor_is_90_in_its_pyproject():
    text = (SUITE_ROOT / "searcher" / "pyproject.toml").read_text(encoding="utf-8")
    assert re.findall(r"--cov-fail-under=(\d+)", text) == [str(FLOORS["searcher"])]


def test_searcher_gates_are_all_present():
    block = _jobs()["searcher-gates"]
    for gate in (
        "ruff format --check .",
        "ruff check .",
        "run: mypy",
        "node --check",
        "node --test tests/js/*.test.js",
        "run: pytest",
    ):
        assert gate in block, f"searcher gates lost {gate!r}"
    assert 'python-version: "3.12"' in block


def test_clipper_ruff_gates_are_present():
    block = _jobs()["clipper-tests"]
    assert "ruff check ." in block
    assert "ruff format --check ." in block


def test_suite_job_runs_suite_tests_and_lint():
    block = _jobs()["suite"]
    assert "ruff check ricesuite tests" in block
    assert "ruff format --check ricesuite tests" in block
    assert "python -m pytest" in block
    text = (SUITE_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "--cov=ricesuite" in text and "--cov-fail-under=90" in text
