"""Offline heuristic scoring (#2): `score --offline` needs no key and no network."""

from __future__ import annotations

import json
import socket
import sys
from pathlib import Path

import pytest

from ricesearcher import cli
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.beat.profile import BeatProfile
from ricesearcher.config import load_config
from ricesearcher.handoff import writer as writer_mod
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateWindow, SliceStatus
from ricesearcher.score.heuristic_scorer import OFFLINE_MODEL_NAME, HeuristicScorer
from tests.conftest import FakeEmbedder, FakeScorer, FakeTranscriber


def _window(score: float, **features: float) -> CandidateWindow:
    return CandidateWindow(
        source_id="s",
        start=0.0,
        end=20.0,
        text="t",
        features=features,
        heuristic_score=score,
    )


def test_heuristic_scorer_uses_prefilter_score_and_says_so() -> None:
    profile = BeatProfile(id="p", name="P", version="v1", brief="b")
    windows = [_window(0.85, keyword=1.0, question=1 / 3), _window(0.2)]
    results = HeuristicScorer().score(windows, profile)
    assert [r.score for r in results] == [0.85, 0.2]
    assert all("not LLM-scored" in r.rationale for r in results)
    assert "(keyword 1, question 0.33)" in results[0].rationale
    assert HeuristicScorer().model_name == OFFLINE_MODEL_NAME == "heuristic-offline"
    assert HeuristicScorer().score([], profile) == []


@pytest.fixture
def offline_env(tmp_path: Path, media_file: Path, monkeypatch) -> str:
    """A pulled source with no credential, no .env, and the LLM path blocked."""
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RICESEARCHER_HANDOFF_DIR", str(tmp_path / "handoff"))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no credentials.env/.env to load
    monkeypatch.setattr(cli, "_default_acquirers", lambda: [WatchFolderAcquirer()])
    monkeypatch.setattr(cli, "WhisperTranscriber", lambda **_: FakeTranscriber())

    def _no_llm(*_a, **_k):
        raise AssertionError("offline scoring constructed the Anthropic scorer")

    monkeypatch.setattr(cli, "AnthropicScorer", _no_llm)

    def _no_network(*_a, **_k):
        raise AssertionError("offline flow attempted a network connection")

    # Blocks any socket connect, not only the Anthropic path we know about.
    monkeypatch.setattr(socket.socket, "connect", _no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", _no_network)
    monkeypatch.setattr(socket, "create_connection", _no_network)
    # A None entry makes any `import anthropic` raise ImportError.
    monkeypatch.setitem(sys.modules, "anthropic", None)
    assert cli.main(["pull", str(media_file)]) == 0
    with Library(load_config().db_path) as lib:
        return lib.list_sources()[0].id


def test_score_offline_needs_no_key_and_never_builds_llm_client(
    offline_env: str, capsys
) -> None:
    rc = cli.main(["score", offline_env[:12], "--profile", "example-beat", "--offline"])
    assert rc == 0, capsys.readouterr().err
    assert "model heuristic-offline" in capsys.readouterr().out
    with Library(load_config().db_path) as lib:
        slices = lib.list_slices(profile_id="example-beat")
    assert slices
    for s in slices:
        assert s.scorer_model == OFFLINE_MODEL_NAME
        assert s.score == s.heuristic_score
        assert "not LLM-scored" in s.rationale


def test_slices_marks_offline_rows_only(offline_env: str, monkeypatch, capsys) -> None:
    assert (
        cli.main(["score", offline_env, "--profile", "example-beat", "--offline"]) == 0
    )
    capsys.readouterr()
    assert cli.main(["slices", "--profile", "example-beat"]) == 0
    assert "offl" in capsys.readouterr().out

    # Re-scoring with an LLM scorer replaces the candidates and drops the marker.
    monkeypatch.setattr(cli, "AnthropicScorer", lambda model=None: FakeScorer())
    assert cli.main(["score", offline_env, "--profile", "example-beat"]) == 0
    capsys.readouterr()
    assert cli.main(["slices", "--profile", "example-beat"]) == 0
    assert "offl" not in capsys.readouterr().out


@pytest.mark.usefixtures("offline_env")
def test_network_guard_blocks_connections() -> None:
    with pytest.raises(AssertionError, match="network connection"):
        socket.create_connection(("127.0.0.1", 9))
    with pytest.raises(AssertionError, match="network connection"):
        socket.socket().connect(("127.0.0.1", 9))


def test_offline_and_model_are_mutually_exclusive(capsys) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(
            ["score", "x", "--profile", "example-beat", "--offline", "--model", "m"]
        )
    assert exc.value.code == 2
    assert "not allowed with" in capsys.readouterr().err


def test_offline_flow_runs_through_dedup_review_and_handoff(
    offline_env: str, monkeypatch, capsys
) -> None:
    """The whole flow runs with no key; dedup stays advisory, select stays manual."""
    from tests.test_handoff import FakeExtractor

    assert (
        cli.main(["score", offline_env, "--profile", "example-beat", "--offline"]) == 0
    )
    monkeypatch.setattr(cli, "SentenceTransformerEmbedder", FakeEmbedder)
    assert cli.main(["dedup", "--profile", "example-beat"]) == 0

    # Nothing is handed off until a human selects a slice.
    monkeypatch.setattr(writer_mod, "FfmpegClipExtractor", FakeExtractor)
    monkeypatch.setattr(writer_mod, "ffprobe_duration", lambda _path: 30.0)
    capsys.readouterr()
    assert cli.main(["handoff", "--profile", "example-beat"]) == 0
    handoff_dir = load_config().handoff_dir
    assert not handoff_dir.exists() or not any(handoff_dir.iterdir())

    with Library(load_config().db_path) as lib:
        chosen = lib.list_slices(profile_id="example-beat")[0]
        assert lib.update_slice_status(chosen.id, SliceStatus.SELECTED)
    assert cli.main(["handoff", "--profile", "example-beat"]) == 0
    assert "handed off 1 clip" in capsys.readouterr().out

    batch = next(p for p in load_config().handoff_dir.iterdir() if p.is_dir())
    manifest = json.loads((batch / "manifest.json").read_text())
    assert manifest["schema_version"] == 1  # additive field; version unchanged
    assert manifest["clips"][0]["scorer_model"] == OFFLINE_MODEL_NAME
