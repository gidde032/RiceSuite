"""Quality-gate meta-tests.

These enforce the project's quality-gate contracts and their *placement*, so a
gate cannot silently disappear the way an undocumented one silently never
existed. They check:

* the smoke tier tags exactly ``SMOKE_TEST_COUNT`` tests,
* the smoke tier stays within its speed budget (it is only useful on the
  pre-commit hook if it is fast), and
* CI runs for pull requests targeting any branch, including stacked PRs, and
* the ``COVERAGE_FLOOR`` and the ruff lint/format gates are enforced in BOTH
  the local pre-push hook and the GitHub Actions workflow, with the coverage
  numbers in agreement.

The coverage/ruff checks inspect the *enforcement mechanism* (the flags in the
hook and CI files) rather than re-running the suite under coverage, which would
double the suite's runtime on every run.
"""

import json
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

PRE_COMMIT_CONFIG = PROJECT_ROOT / ".pre-commit-config.yaml"
CI_WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
RULESET = PROJECT_ROOT / ".github" / "rulesets" / "main.json"
DEV_REQUIREMENTS = PROJECT_ROOT / "requirements-dev.txt"

# The single required check. Renaming it would silently orphan the ruleset.
REQUIRED_CHECK = "Python 3.12 tests and coverage"
# The newest Python the README declares supported; CI must exercise it.
NEWEST_SUPPORTED_PYTHON = "3.14"

COVERAGE_FLOOR = 85
SMOKE_TEST_COUNT = 8

# The smoke tier's execution budget. It runs in ~0.35s today, so this leaves
# generous headroom for a loaded machine while still catching a tier that has
# quietly accreted slow tests and is no longer commit-hook material.
SMOKE_EXECUTION_BUDGET_S = 3.0


def test_ci_runs_for_pull_requests_to_any_branch():
    """The CI workflow must not exclude stacked PRs targeting feature branches."""
    ci = CI_WORKFLOW.read_text(encoding="utf-8")
    pull_request_trigger = ci.split("pull_request:", maxsplit=1)[1].split(
        "push:", maxsplit=1
    )[0]

    assert "branches:" not in pull_request_trigger, (
        "CI filters pull_request target branches, so stacked PRs receive no checks"
    )
    assert "branches-ignore:" not in pull_request_trigger, (
        "CI ignores pull request target branches, so some stacked PRs receive no checks"
    )


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


def _ci_job_blocks(ci: str) -> dict[str, str]:
    """Map each CI job's ``name:`` to the text of its job block."""
    jobs_text = ci.split("\njobs:\n", maxsplit=1)[1]
    blocks = re.split(r"\n(?=  [A-Za-z0-9_-]+:\n)", "\n" + jobs_text)
    named = {}
    for block in blocks:
        match = re.search(r"^    name: (.+)$", block, flags=re.MULTILINE)
        if match:
            named[match.group(1).strip()] = block
    return named


def test_required_check_name_matches_ci_and_ruleset():
    """The ruleset's required check must still be a real CI job name."""
    jobs = _ci_job_blocks(CI_WORKFLOW.read_text(encoding="utf-8"))
    ruleset = json.loads(RULESET.read_text(encoding="utf-8"))
    required = [
        check["context"]
        for rule in ruleset["rules"]
        if rule["type"] == "required_status_checks"
        for check in rule["parameters"]["required_status_checks"]
    ]
    assert required == [REQUIRED_CHECK]
    assert REQUIRED_CHECK in jobs, f"no CI job named {REQUIRED_CHECK!r}"


def test_ci_runs_newest_supported_python_as_non_required_job():
    """CI must run the suite on the newest supported Python, outside the ruleset.

    The README supports 3.11-3.14 but the required job only runs 3.12; the
    maintainer develops on 3.14. A separate, non-required job keeps 3.14
    regressions visible without changing the required check's name.
    """
    jobs = _ci_job_blocks(CI_WORKFLOW.read_text(encoding="utf-8"))
    newest = [
        name
        for name, block in jobs.items()
        if f'python-version: "{NEWEST_SUPPORTED_PYTHON}"' in block
    ]
    assert newest, f"no CI job runs Python {NEWEST_SUPPORTED_PYTHON}"
    ruleset = RULESET.read_text(encoding="utf-8")
    for name in newest:
        assert name != REQUIRED_CHECK
        assert f'"{name}"' not in ruleset, f"{name!r} must stay non-required"
        assert "python -m pytest tests/" in jobs[name]


def test_pre_commit_tool_is_pinned_in_dev_requirements():
    """The documented pre-commit hooks must be installable from requirements-dev."""
    assert PRE_COMMIT_CONFIG.exists()
    pins = DEV_REQUIREMENTS.read_text(encoding="utf-8")
    assert re.search(r"^pre-commit==\d+\.\d+\.\d+", pins, flags=re.MULTILINE), (
        "requirements-dev.txt does not pin pre-commit, so the documented "
        "'pre-commit install' fails in a fresh dev venv"
    )
