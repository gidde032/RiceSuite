"""CLI `score` and `slices` commands (FR-4/5, D7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher import cli
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from tests.conftest import FakeScorer, FakeTranscriber


@pytest.fixture
def pulled_source(tmp_path: Path, media_file: Path, monkeypatch) -> str:
    """Pull a source into a temp library and return its id prefix."""
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "_default_acquirers", lambda: [WatchFolderAcquirer()])
    monkeypatch.setattr(cli, "WhisperTranscriber", lambda **_: FakeTranscriber())
    assert cli.main(["pull", str(media_file)]) == 0
    from ricesearcher.config import load_config
    from ricesearcher.library.store import Library

    with Library(load_config().db_path) as lib:
        return lib.list_sources()[0].id


def test_score_then_slices(pulled_source: str, monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "AnthropicScorer", lambda model=None: FakeScorer())
    rc = cli.main(["score", pulled_source[:12], "--profile", "example-beat"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "scored" in out and "fake-model" in out

    assert cli.main(["slices", "--profile", "example-beat"]) == 0
    listing = capsys.readouterr().out
    assert listing.strip()  # at least one slice line


def test_score_unknown_source_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "AnthropicScorer", lambda model=None: FakeScorer())
    assert cli.main(["score", "deadbeef", "--profile", "example-beat"]) == 2
    assert "no source" in capsys.readouterr().err


def test_score_reports_clean_error_on_scorer_failure(
    pulled_source: str, monkeypatch, capsys
) -> None:
    class BoomScorer:
        model_name = "boom"

        def score(self, windows, profile):
            raise RuntimeError("api down")

    monkeypatch.setattr(cli, "AnthropicScorer", lambda model=None: BoomScorer())
    rc = cli.main(["score", pulled_source[:12], "--profile", "example-beat"])
    assert rc == 2
    assert "error" in capsys.readouterr().err.lower()


def test_slices_empty(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    assert cli.main(["slices", "--profile", "example-beat"]) == 0
    assert "no scored slices" in capsys.readouterr().out


def test_score_without_api_key_names_the_missing_variable(
    pulled_source: str, tmp_path: Path, monkeypatch, capsys
) -> None:
    """The real scorer fails before any request with an actionable message."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no credentials.env/.env to load
    rc = cli.main(["score", pulled_source[:12], "--profile", "example-beat"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "ANTHROPIC_API_KEY is not set" in err
    assert "credentials.env" in err
