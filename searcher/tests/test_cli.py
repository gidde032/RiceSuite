"""CLI commands (SPEC §8, D7). Uses a temp data dir via env override."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher import cli
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer


@pytest.fixture
def data_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def test_pull_and_list_and_show(
    data_env: Path, media_file: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    # Route through the real watch-folder acquirer but a fake transcriber.
    from tests.conftest import FakeTranscriber

    monkeypatch.setattr(cli, "_default_acquirers", lambda: [WatchFolderAcquirer()])
    monkeypatch.setattr(cli, "WhisperTranscriber", lambda **_: FakeTranscriber())

    assert cli.main(["pull", str(media_file)]) == 0
    out = capsys.readouterr().out
    assert "pulled" in out

    assert cli.main(["list"]) == 0
    listing = capsys.readouterr().out
    assert "clip" in listing  # title is the file stem

    # Grab the printed id prefix and show it.
    sid = listing.split()[0]
    assert cli.main(["show", sid]) == 0
    shown = capsys.readouterr().out
    assert "hello world" in shown


def test_pull_unhandled_request_errors(
    data_env: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_default_acquirers", lambda: [WatchFolderAcquirer()])
    rc = cli.main(["pull", "https://youtu.be/only-url-no-download"])
    # No acquirer downloads here (ytdlp excluded), so watch-folder rejects it.
    assert rc == 2
    assert "error" in capsys.readouterr().err


def test_show_missing_source_errors(data_env: Path, capsys) -> None:
    assert cli.main(["show", "deadbeef"]) == 2
    assert "no source" in capsys.readouterr().err


def test_list_empty(data_env: Path, capsys) -> None:
    assert cli.main(["list"]) == 0
    assert "empty" in capsys.readouterr().out


def test_build_parser_requires_command() -> None:
    with pytest.raises(SystemExit):
        cli.main([])


@pytest.mark.parametrize("command", ["score", "slices", "dedup", "handoff"])
def test_profile_commands_reject_invalid_ids(
    command: str, data_env: Path, capsys
) -> None:
    argv = [command]
    if command == "score":
        argv.append("missing-source")
    argv += ["--profile", "bad_id"]

    with pytest.raises(SystemExit) as exc:
        cli.main(argv)

    assert exc.value.code == 2
    assert "invalid profile id" in capsys.readouterr().err


@pytest.mark.parametrize("host", ["0.0.0.0", "::1", "192.168.1.5"])
def test_review_refuses_a_host_its_guard_would_never_answer(host: str) -> None:
    """The review app answers only 127.0.0.1 / localhost Hosts (suite SPEC
    FR-3), so binding anywhere else would print a URL that always gets 421."""
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["review", "--host", host])


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_review_accepts_a_loopback_host(host: str) -> None:
    assert cli.build_parser().parse_args(["review", "--host", host]).host == host
