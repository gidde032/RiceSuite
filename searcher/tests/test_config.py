"""Local env-file loading for secrets (ANTHROPIC_API_KEY etc.)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ricesearcher.config import load_env_files


@pytest.fixture
def in_tmp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_loads_credentials_env(in_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    (in_tmp / "credentials.env").write_text(
        "# a comment\nANTHROPIC_API_KEY=sk-test-123\n\nBLANK\n"
    )
    load_env_files()
    assert os.environ["ANTHROPIC_API_KEY"] == "sk-test-123"


def test_explicit_export_wins_over_file(
    in_tmp: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "real-exported")
    (in_tmp / "credentials.env").write_text("ANTHROPIC_API_KEY=from-file")
    load_env_files()
    assert os.environ["ANTHROPIC_API_KEY"] == "real-exported"


def test_strips_quotes(in_tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RICESEARCHER_SCORER_MODEL", raising=False)
    (in_tmp / ".env").write_text('RICESEARCHER_SCORER_MODEL="claude-haiku-4-5"')
    load_env_files()
    assert os.environ["RICESEARCHER_SCORER_MODEL"] == "claude-haiku-4-5"


def test_no_file_is_noop(in_tmp: Path) -> None:
    load_env_files()  # must not raise when neither file exists
