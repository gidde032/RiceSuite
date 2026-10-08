"""Standalone Clipper uses the paths ``rice data location`` reports.

Follow-up to RiceSuite #73/#74: after ``rice data cutover`` (or on a fresh
install) the launcher passes ``<root>/clipper`` and the unified handoff stages,
and standalone Searcher and Poster now follow them, but standalone Clipper kept
reading ``~/ricesearcher-handoff``, writing ``~/riceclipper-handoff``, and
storing jobs in the checkout's ``.riceclipper_work``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app import handoff, jobs, searcher_pickup


def _cut_over(tmp_path: Path, *lines: str) -> Path:
    """A completed cutover to ``tmp_path/suite`` with a legacy copy retained."""
    root = tmp_path / "suite"
    root.mkdir()
    (root / ".cutover.json").write_text(
        json.dumps({"version": 1, "digest": "d", "phase": "complete"}),
        encoding="utf-8",
    )
    (Path.home() / "ricesearcher-handoff").mkdir()
    Path(os.environ["RICESUITE_ENV"]).write_text(
        "\n".join([f"RICESUITE_DATA_DIR={root}", *lines]) + "\n", encoding="utf-8"
    )
    return root


def _interrupt(root: Path) -> None:
    (root / ".cutover.json").write_text(
        json.dumps({"version": 1, "digest": "d", "phase": "pending"}),
        encoding="utf-8",
    )


@pytest.fixture
def unset_work_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs, "WORK_ROOT", None)


@pytest.mark.usefixtures("unset_work_root")
def test_unset_paths_follow_the_suite_after_cutover(tmp_path: Path) -> None:
    root = _cut_over(tmp_path)
    assert searcher_pickup.inbox_root() == root / "handoff" / "searcher-to-clipper"
    assert handoff.handoff_root() == root / "handoff" / "clipper-to-poster"
    assert jobs.work_root() == root / "clipper"


@pytest.mark.usefixtures("unset_work_root")
def test_legacy_install_keeps_the_old_paths() -> None:
    (Path.home() / "ricesearcher-handoff").mkdir()
    assert searcher_pickup.inbox_root() == Path.home() / "ricesearcher-handoff"
    assert handoff.handoff_root() == Path.home() / "riceclipper-handoff"
    checkout = Path(jobs.__file__).resolve().parents[1]
    assert jobs.work_root() == checkout / ".riceclipper_work"


@pytest.mark.usefixtures("unset_work_root")
def test_explicit_paths_win_and_blank_counts_as_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _cut_over(tmp_path)
    monkeypatch.setenv("RICECLIPPER_SEARCHER_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", "  ")
    monkeypatch.setenv("RICECLIPPER_WORK_DIR", str(tmp_path / "work"))
    assert searcher_pickup.inbox_root() == tmp_path / "inbox"
    assert handoff.handoff_root() == root / "handoff" / "clipper-to-poster"
    assert jobs.work_root() == tmp_path / "work"


@pytest.mark.usefixtures("unset_work_root")
def test_the_inbox_follows_searchers_end_of_the_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The suite derives both ends of a stage from either one (FR-8).
    _cut_over(tmp_path, f"RICESEARCHER_HANDOFF_DIR={tmp_path / 'stage'}")
    assert searcher_pickup.inbox_root() == tmp_path / "stage"


@pytest.mark.usefixtures("unset_work_root")
def test_explicit_paths_do_not_consult_the_suite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # `rice start` passes every path. An unrelated suite problem (here an
    # interrupted cutover) must not stop a child that already has them.
    _interrupt(_cut_over(tmp_path))
    monkeypatch.setenv("RICECLIPPER_SEARCHER_INBOX", str(tmp_path / "inbox"))
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("RICECLIPPER_WORK_DIR", str(tmp_path / "work"))
    assert searcher_pickup.inbox_root() == tmp_path / "inbox"
    assert handoff.handoff_root() == tmp_path / "out"
    assert jobs.work_root() == tmp_path / "work"


@pytest.mark.usefixtures("unset_work_root")
def test_startup_refuses_an_invalid_suite_configuration(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from app.main import app

    _interrupt(_cut_over(tmp_path))
    with pytest.raises(Exception, match="data cutover was interrupted"):
        with TestClient(app):
            pass


def test_inherited_suite_paths_are_cleared() -> None:
    # The fixture must drop every path the resolver reads, or an exported
    # Poster HANDOFF_DIR redirects Clipper's output in a test.
    from ricesuite.env import DATA_PATHS

    leaked = {*DATA_PATHS, "RICECLIPPER_SEARCHER_INBOX", "HANDOFF_DIR"} & {*os.environ}
    assert not leaked


def test_env_example_does_not_pin_the_data_paths() -> None:
    # A .env copied from the example counts as explicit, so a pinned legacy
    # path would bypass the suite resolution in standalone Clipper.
    example = Path(jobs.__file__).resolve().parents[1] / ".env.example"
    assigned = {
        line.partition("=")[0].strip()
        for line in example.read_text(encoding="utf-8").splitlines()
        if "=" in line and not line.lstrip().startswith("#")
    }
    assert not assigned & {
        "RICECLIPPER_SEARCHER_INBOX",
        "RICECLIPPER_HANDOFF_DIR",
        "RICECLIPPER_WORK_DIR",
    }
