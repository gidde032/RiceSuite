"""Privacy pin: only the neutral header prompt may be committed.

A maintainer-specific header style names real people and would tie this repo to
the accounts it serves, so it must stay local and gitignored (mirrors
RicePoster's tracked-prompts pin). This test fails loudly if any other prompt
file is ever tracked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_only_the_neutral_header_prompt_is_tracked():
    tracked = sorted(
        line
        for line in subprocess.run(
            ["git", "ls-files", "prompts/"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if line.strip()
    )
    assert tracked == ["prompts/generic-header.json"], (
        "Only the neutral header prompt may be committed; a persona-specific "
        f"style must stay local and gitignored. Tracked: {tracked}"
    )
