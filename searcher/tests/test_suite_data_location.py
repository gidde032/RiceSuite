"""Standalone Searcher uses the paths ``rice data location`` reports (#73).

After ``rice data cutover`` the launcher passes ``<root>/searcher``, but the
standalone ``ricesearcher`` CLI fell back to ``~/.ricesearcher``: the stale
pre-cutover copy. ``score --profile`` then missed profiles created since, and
handoff batches went where Clipper no longer looks.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ricesearcher.cli import main
from ricesearcher.config import load_config


def _cut_over(tmp_path: Path, *lines: str) -> Path:
    """A completed cutover to ``tmp_path/suite`` with a legacy copy retained."""
    root = tmp_path / "suite"
    root.mkdir()
    (root / ".cutover.json").write_text(
        json.dumps({"version": 1, "digest": "d", "phase": "complete"}),
        encoding="utf-8",
    )
    (Path.home() / ".ricesearcher" / "profiles").mkdir(parents=True)
    Path(os.environ["RICESUITE_ENV"]).write_text(
        "\n".join([f"RICESUITE_DATA_DIR={root}", *lines]) + "\n", encoding="utf-8"
    )
    return root


def _profile(directory: Path, profile_id: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{profile_id}.json").write_text(
        json.dumps({"version": "v1", "name": profile_id, "brief": "b"}),
        encoding="utf-8",
    )


def test_unset_paths_follow_the_suite_after_cutover(tmp_path: Path) -> None:
    root = _cut_over(tmp_path)
    cfg = load_config()
    assert cfg.data_dir == root / "searcher"
    assert cfg.handoff_dir == root / "handoff" / "searcher-to-clipper"
    assert cfg.profiles_dir == root / "searcher" / "profiles"


def test_score_finds_a_profile_created_after_cutover(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _cut_over(tmp_path)
    _profile(root / "searcher" / "profiles", "rap-edits")
    assert main(["score", "nosuchsource", "--profile", "rap-edits"]) == 2
    err = capsys.readouterr().err
    assert "profile" not in err
    assert "no source 'nosuchsource'" in err
    assert not (Path.home() / ".ricesearcher" / "library.sqlite3").exists()


def test_legacy_install_keeps_the_old_paths(tmp_path: Path) -> None:
    (Path.home() / ".ricesearcher").mkdir()
    cfg = load_config()
    assert cfg.data_dir == Path.home() / ".ricesearcher"
    assert cfg.handoff_dir == Path.home() / "ricesearcher-handoff"


def test_shell_paths_win_and_blank_counts_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _cut_over(tmp_path)
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "mine"))
    monkeypatch.setenv("RICESEARCHER_HANDOFF_DIR", "  ")
    cfg = load_config()
    assert cfg.data_dir == tmp_path / "mine"
    assert cfg.handoff_dir == root / "handoff" / "searcher-to-clipper"


def test_profiles_dir_from_the_suite_file_and_the_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cutover with custom profiles writes RICESEARCHER_PROFILES_DIR into
    # ricesuite.env (ricesuite.migration); the shell still wins over it.
    _cut_over(tmp_path, f"RICESEARCHER_PROFILES_DIR={tmp_path / 'filed'}")
    assert load_config().profiles_dir == tmp_path / "filed"
    monkeypatch.setenv("RICESEARCHER_PROFILES_DIR", str(tmp_path / "shell"))
    assert load_config().profiles_dir == tmp_path / "shell"


def test_suite_configuration_error_is_a_clean_cli_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _cut_over(tmp_path)
    (root / ".cutover.json").write_text(
        json.dumps({"version": 1, "digest": "d", "phase": "pending"}),
        encoding="utf-8",
    )
    assert main(["profiles"]) == 2
    assert "RiceSuite configuration: data cutover was interrupted" in (
        capsys.readouterr().err
    )


def test_explicit_paths_do_not_consult_the_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `rice start` passes both paths. An unrelated suite problem (here an
    # interrupted cutover) must not stop a child that already has them.
    root = _cut_over(tmp_path)
    (root / ".cutover.json").write_text(
        json.dumps({"version": 1, "digest": "d", "phase": "pending"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RICESEARCHER_HANDOFF_DIR", str(tmp_path / "handoff"))
    cfg = load_config()
    assert cfg.data_dir == tmp_path / "data"
    assert cfg.handoff_dir == tmp_path / "handoff"
    assert cfg.profiles_dir == tmp_path / "data" / "profiles"
