"""Shared construction policies use fakes and never contact external services."""

import asyncio
import sys
from types import ModuleType, SimpleNamespace

import pytest

from ricesuite import anthropic_client, whisper


def test_anthropic_construction_policy_and_auth_boundary(monkeypatch):
    calls = []
    fake = ModuleType("anthropic")

    def constructor(kind):
        def build(**options):
            calls.append((kind, options))
            return SimpleNamespace(kind=kind)

        return build

    fake.Anthropic = constructor("sync")
    fake.AsyncAnthropic = constructor("async")
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    assert anthropic_client.create_client(api_key="key").kind == "sync"
    assert (
        anthropic_client.create_client(api_key="key", asynchronous=True).kind == "async"
    )
    assert anthropic_client.create_client(auth_token="token").kind == "sync"
    assert calls == [
        ("sync", {"api_key": "key", "timeout": 60.0, "max_retries": 2}),
        ("async", {"api_key": "key", "timeout": 60.0, "max_retries": 2}),
        ("sync", {"auth_token": "token", "timeout": 60.0, "max_retries": 2}),
    ]


def test_whisper_construction_preserves_caller_options(monkeypatch):
    calls = []
    fake = ModuleType("faster_whisper")
    fake.WhisperModel = lambda *args, **kwargs: calls.append((args, kwargs))
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    whisper.create_model("small", compute_type="int8")
    whisper.create_model(
        "medium",
        compute_type="float16",
        device="cpu",
        cpu_threads=3,
        before_create=lambda: calls.append("configured"),
    )
    assert calls == [
        (("small",), {"compute_type": "int8"}),
        "configured",
        (("medium",), {"compute_type": "float16", "device": "cpu", "cpu_threads": 3}),
    ]


def test_whisper_consumes_lazy_words_and_propagates_iteration_error():
    words = [SimpleNamespace(word=" one "), SimpleNamespace(word=" ")]

    class Model:
        def transcribe(self, path, *, word_timestamps):
            assert (path, word_timestamps) == ("media.mp4", True)

            def segments():
                yield SimpleNamespace(words=words)
                yield SimpleNamespace(words=None)

            return segments(), None

    assert whisper.transcribe_words(Model(), "media.mp4") == words

    class Broken:
        def transcribe(self, path, *, word_timestamps):
            def segments():
                raise ValueError("lazy decode failed")
                yield

            return segments(), None

    with pytest.raises(ValueError, match="lazy decode failed"):
        whisper.transcribe_words(Broken(), "media.mp4")


def test_shared_modules_import_without_heavy_clients():
    assert "faster_whisper" not in whisper.__dict__
    assert "anthropic" not in anthropic_client.__dict__


def test_cleanup_helpers_do_not_expose_errors(caplog):
    class SyncClient:
        def close(self):
            raise RuntimeError("private cleanup detail")

    class AsyncClient:
        async def close(self):
            raise RuntimeError("private cleanup detail")

    anthropic_client.close_client(SyncClient())
    anthropic_client.close_client(SimpleNamespace())
    asyncio.run(anthropic_client.close_async_client(AsyncClient()))
    asyncio.run(anthropic_client.close_async_client(SimpleNamespace()))
    assert caplog.text.count("RuntimeError") == 2
    assert "private cleanup detail" not in caplog.text


def test_cleanup_property_failure_does_not_replace_primary_result():
    class BrokenCloseProperty:
        @property
        def close(self):
            raise RuntimeError("property failed")

    anthropic_client.close_client(BrokenCloseProperty())
    asyncio.run(anthropic_client.close_async_client(BrokenCloseProperty()))
