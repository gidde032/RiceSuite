"""Shared fixtures + smoke-tier registration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.models import SourceKind, TranscriptWord
from tests.smoke_registry import SMOKE_NODEIDS


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "smoke: fast-feedback tier")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    SMOKE_NODEIDS.clear()
    for item in items:
        if item.get_closest_marker("smoke"):
            SMOKE_NODEIDS.append(item.nodeid)


@pytest.fixture(autouse=True)
def _hermetic_ricesearcher_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop RICESEARCHER_* settings inherited from the developer's shell.

    Tests set the paths they need; an exported RICESEARCHER_PROFILES_DIR would
    otherwise redirect profile seeding into the developer's real directory.
    """
    for key in [k for k in os.environ if k.startswith("RICESEARCHER_")]:
        monkeypatch.delenv(key)


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    """A tiny fake media file with real bytes for content hashing."""
    p = tmp_path / "clip.mp4"
    p.write_bytes(b"\x00\x01fake-media-bytes\x02\x03")
    return p


class FakeAcquirer:
    """An acquirer that hands back a prepared local file."""

    def __init__(self, media_path: Path, kind: SourceKind = SourceKind.LOCAL) -> None:
        self._media_path = media_path
        self._kind = kind

    def can_handle(self, request: str) -> bool:
        return request == "fake"

    def acquire(self, request: str) -> AcquiredSource:
        return AcquiredSource(
            kind=self._kind,
            ref=request,
            media_path=self._media_path,
            title="Fake Source",
            channel="Fake Channel",
            duration_s=42.0,
        )


class FakeTranscriber:
    """A transcriber returning fixed words, no model needed."""

    def transcribe(self, media_path: Path) -> list[TranscriptWord]:
        return [
            TranscriptWord("hello", 0.0, 0.4),
            TranscriptWord("world", 0.4, 0.9),
        ]


class FakeScorer:
    """A scorer returning deterministic ascending scores, no LLM needed."""

    model_name = "fake-model"

    def score(self, windows, profile):
        from ricesearcher.score.base import ScoredResult

        return [
            ScoredResult(score=min(1.0, 0.4 + 0.1 * i), rationale=f"reason {i}")
            for i, _ in enumerate(windows)
        ]


@pytest.fixture
def fake_acquirer(media_file: Path) -> FakeAcquirer:
    return FakeAcquirer(media_file)


@pytest.fixture
def fake_transcriber() -> FakeTranscriber:
    return FakeTranscriber()


class FakeEmbedder:
    """Deterministic one-hot embedder: identical texts -> cosine 1.0, else 0.0."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        vocab: dict[str, int] = {}
        for t in texts:
            vocab.setdefault(t, len(vocab))
        dim = max(len(vocab), 1)
        out = []
        for t in texts:
            v = [0.0] * dim
            v[vocab[t]] = 1.0
            out.append(v)
        return out


@pytest.fixture
def fake_scorer() -> FakeScorer:
    return FakeScorer()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()
