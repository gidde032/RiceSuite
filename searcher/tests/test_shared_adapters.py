"""Searcher keeps its transcription and scoring contracts with shared mechanics."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow, TranscriptWord
from ricesearcher.score import anthropic_scorer
from ricesearcher.transcribe.whisper import WhisperTranscriber


def test_whisper_keeps_blank_words_and_native_timestamps():
    class Model:
        def transcribe(self, path, *, word_timestamps):
            assert (path, word_timestamps) == ("clip.mp4", True)
            return iter(
                [
                    SimpleNamespace(
                        words=[
                            SimpleNamespace(word=" hi ", start=1, end=2),
                            SimpleNamespace(word="  ", start=2, end=3),
                        ]
                    )
                ]
            ), None

    adapter = WhisperTranscriber()
    adapter._model = Model()
    assert adapter.transcribe(Path("clip.mp4")) == [
        TranscriptWord(text="hi", start=1, end=2),
        TranscriptWord(text="", start=2, end=3),
    ]


def test_whisper_empty_and_lazy_audio_failure():
    class Empty:
        def transcribe(self, path, *, word_timestamps):
            return iter([SimpleNamespace(words=None)]), None

    adapter = WhisperTranscriber()
    adapter._model = Empty()
    assert adapter.transcribe(Path("clip.mp4")) == []

    class Broken:
        def transcribe(self, path, *, word_timestamps):
            def segments():
                raise IndexError("no stream")
                yield

            return segments(), None

    adapter._model = Broken()
    with pytest.raises(RuntimeError, match="no decodable audio stream in clip.mp4"):
        adapter.transcribe(Path("clip.mp4"))


def test_scorer_closes_client_and_keeps_auth_token_compatibility(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
    seen = []

    class Client:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            seen.append(kwargs)
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="text", text='[{"index":0,"score":0.5,"rationale":"ok"}]'
                    )
                ]
            )

        def close(self):
            seen.append("closed")

    def build(**kwargs):
        assert kwargs == {"api_key": None, "auth_token": "token"}
        return Client()

    monkeypatch.setattr(anthropic_scorer, "create_client", build)
    profile = BeatProfile(version="1", name="beat", brief="brief")
    windows = [CandidateWindow(source_id="s", start=0, end=2, text="hello")]
    assert anthropic_scorer.AnthropicScorer().score(windows, profile)[0].score == 0.5
    assert seen[-1] == "closed"


def test_scorer_closes_client_on_request_failure(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key")
    closed = []

    class Client:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            raise ValueError("request failed")

        def close(self):
            closed.append(True)

    monkeypatch.setattr(anthropic_scorer, "create_client", lambda **kwargs: Client())
    profile = BeatProfile(version="1", name="beat", brief="brief")
    windows = [CandidateWindow(source_id="s", start=0, end=2, text="hello")]
    with pytest.raises(ValueError, match="request failed"):
        anthropic_scorer.AnthropicScorer().score(windows, profile)
    assert closed == [True]
