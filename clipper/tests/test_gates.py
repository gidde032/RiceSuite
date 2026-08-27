"""Quality-gate meta-tests.

These enforce the project's numeric gate contracts and their *placement*, so a
gate cannot silently disappear the way an undocumented one silently never
existed. They check:

* the smoke tier tags exactly ``SMOKE_TEST_COUNT`` tests,
* the smoke tier stays within its speed budget (it is only useful on the
  pre-commit hook if it is fast), and
* the ``COVERAGE_FLOOR`` and the ruff lint/format gates are enforced in BOTH
  the local pre-push hook and the GitHub Actions workflow, with the coverage
  numbers in agreement.

The coverage/ruff checks inspect the *enforcement mechanism* (the flags in the
hook and CI files) rather than re-running the suite under coverage, which would
double the suite's runtime on every run.
"""

import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PRE_COMMIT_CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"

COVERAGE_FLOOR = 85
SMOKE_TEST_COUNT = 6

# The smoke tier's execution budget. It runs in ~0.35s today, so this leaves
# generous headroom for a loaded machine while still catching a tier that has
# quietly accreted slow tests and is no longer commit-hook material.
SMOKE_EXECUTION_BUDGET_S = 3.0


def _collected_node_ids(stdout: str) -> list[str]:
    """The test node IDs pytest printed.

    ``--collect-only -q`` prints one node ID per selected test followed by a
    summary sentence; every real line contains ``::``. Counting node IDs is a
    stable public contract (each can be pasted back to pytest as an argument),
    unlike the summary prose pytest is free to reword between releases.
    """
    return [line.strip() for line in stdout.splitlines() if "::" in line]


def test_smoke_marker_exact_count():
    """The 'smoke' marker must tag exactly SMOKE_TEST_COUNT tests."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-m", "smoke", "--collect-only", "-q"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    node_ids = _collected_node_ids(result.stdout)
    assert len(node_ids) == SMOKE_TEST_COUNT, (
        f"expected {SMOKE_TEST_COUNT} smoke tests, found {len(node_ids)}:\n"
        + "\n".join(node_ids)
    )


def test_smoke_tier_stays_within_budget():
    """The smoke tier must execute within its speed budget."""
    with tempfile.TemporaryDirectory() as tmp:
        junit = Path(tmp) / "smoke.xml"
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pytest",
                "-m",
                "smoke",
                "-q",
                "--no-header",
                f"--junit-xml={junit}",
            ],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        root = ET.parse(junit).getroot()
        suite = root.find("testsuite") if root.tag != "testsuite" else root
        elapsed = float(suite.get("time"))
    assert elapsed < SMOKE_EXECUTION_BUDGET_S, (
        f"smoke tier took {elapsed:.2f}s, over the {SMOKE_EXECUTION_BUDGET_S}s budget"
    )


def _cov_floors(text: str) -> list[int]:
    return [int(n) for n in re.findall(r"--cov-fail-under=(\d+)", text)]


def test_coverage_floor_enforced_and_agrees_across_hook_and_ci():
    """The 85% floor must be enforced in the pre-push hook AND in CI, and match.

    This is the only thing keeping the documented coverage floor from becoming
    a number that runs nowhere.
    """
    hook = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    ci = CI_WORKFLOW.read_text(encoding="utf-8")

    hook_floors = _cov_floors(hook)
    ci_floors = _cov_floors(ci)

    assert hook_floors, "no --cov-fail-under in .pre-commit-config.yaml"
    assert ci_floors, "no --cov-fail-under in the CI workflow"

    for source, floors in (("hook", hook_floors), ("CI", ci_floors)):
        for value in floors:
            assert value == COVERAGE_FLOOR, (
                f"{source} coverage floor is {value}, expected {COVERAGE_FLOOR}"
            )


def test_ruff_gates_present_in_hook_and_ci():
    """Ruff lint AND format must be gated in both the pre-commit hook and CI."""
    hook = PRE_COMMIT_CONFIG.read_text(encoding="utf-8")
    ci = CI_WORKFLOW.read_text(encoding="utf-8")

    assert "ruff check" in hook, "pre-commit hook does not run 'ruff check'"
    assert "ruff format --check" in hook, "pre-commit hook does not gate formatting"
    assert "ruff check" in ci, "CI does not run 'ruff check'"
    assert "ruff format --check" in ci, "CI does not gate formatting"
