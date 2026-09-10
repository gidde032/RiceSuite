"""Stable-documentation regressions (S4 README, S5 handoff dedup)."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent


def test_readme_documents_cli_commands() -> None:
    readme = (_ROOT / "README.md").read_text()
    for token in ("ricesearcher pull", "ricesearcher list", "ricesearcher show"):
        assert token in readme, f"README must document `{token}`"


def test_readme_documents_runnable_smoke_command() -> None:
    readme = (_ROOT / "README.md").read_text()
    assert "pytest -m smoke --no-cov" in readme


def test_handoff_has_single_reserved_section() -> None:
    handoff = (_ROOT / "handoff.md").read_text()
    assert handoff.count("## Reserved from the agent (maintainer-only)") == 1
