"""Run the page-script behaviour tests in tests/js with node's test runner.

These drive Clipper's automatic-transport code (ADR-001 Q12) and Poster's
inbox and draft code, which never pulls without a click (Q12 as amended
2026-09-29). Node is required, not optional: a missing node fails here rather than
skipping the only behavioural check on those rules.
"""

import shutil
import subprocess

from ricesuite import SUITE_ROOT


def test_page_scripts_behave():
    node = shutil.which("node")
    assert node, "node is required to run tests/js (install Node 20+)"
    files = sorted(str(p) for p in (SUITE_ROOT / "tests" / "js").glob("*.test.js"))
    assert len(files) >= 2
    result = subprocess.run(
        [node, "--test", *files], capture_output=True, text=True, timeout=300
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
    assert "ℹ fail 0" in result.stdout or "# fail 0" in result.stdout
