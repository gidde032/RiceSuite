"""Execute UI behavior contracts without launching the app or reading live data."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_progress_ui_behaviors():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node is required for the progress UI behavior checks")
    result = subprocess.run(
        [node, "--test", str(Path(__file__).with_name("progress-ui.test.cjs"))],
        text=True,
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
