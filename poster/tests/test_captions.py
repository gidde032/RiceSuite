"""Unit tests for backend/captions.py with the Anthropic client mocked."""

import asyncio
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from backend import captions


class _FakeAsyncAnthropic:
    """Stands in for anthropic.AsyncAnthropic; returns a canned caption."""

    created_with = []
    init_kwargs = []

    def __init__(self, api_key=None, **kwargs):
        # **kwargs mirrors the real client, which accepts timeout/max_retries
        # alongside api_key. Recorded rather than swallowed so the bounded
        # timeout (tech-debt audit BE-10) is assertable.
        _FakeAsyncAnthropic.created_with.append(api_key)
        _FakeAsyncAnthropic.init_kwargs.append(kwargs)
        self.messages = self

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        block = SimpleNamespace(type="text", text="  a fine caption  ")
        return SimpleNamespace(content=[block])


@pytest.fixture
def fake_anthropic(monkeypatch):
    _FakeAsyncAnthropic.created_with = []
    _FakeAsyncAnthropic.init_kwargs = []
    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", _FakeAsyncAnthropic)
    return _FakeAsyncAnthropic


def test_build_user_prompt_with_topic():
    p = captions._build_user_prompt("video", "rice cooking", "fallback text")
    assert "video" in p
    assert "rice cooking" in p
    assert "fallback text" not in p


def test_build_user_prompt_without_topic_falls_back():
    p = captions._build_user_prompt("image", "   ", "No specific topic provided. Generic.")
    assert "No specific topic provided" in p


def test_generate_caption_returns_stripped_text(fake_anthropic, monkeypatch):
    monkeypatch.setattr(captions, "ANTHROPIC_API_KEY", SecretStr("test-key"))
    result = asyncio.run(captions.generate_caption("video", "topic"))
    assert result == "a fine caption"


def test_owned_async_client_closes_on_success_and_failure(monkeypatch):
    monkeypatch.setattr(captions, "ANTHROPIC_API_KEY", SecretStr("fake-key"))
    closed = []

    class Client:
        def __init__(self, *, api_key=None, **kwargs):
            self.messages = self

        async def create(self, **kwargs):
            block = SimpleNamespace(type="text", text="caption")
            return SimpleNamespace(content=[block])

        async def close(self):
            closed.append(True)

    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", Client)
    assert asyncio.run(captions.generate_caption("video", "topic")) == "caption"
    assert closed == [True]

    class Failing(Client):
        async def create(self, **kwargs):
            raise RuntimeError("service unavailable")

    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", Failing)
    with pytest.raises(RuntimeError, match="service unavailable"):
        asyncio.run(captions.generate_caption("video", "topic"))
    assert closed == [True, True]


def test_cleanup_failure_preserves_caption_and_request_error(monkeypatch):
    monkeypatch.setattr(captions, "ANTHROPIC_API_KEY", SecretStr("fake-key"))

    class BadClose:
        def __init__(self, *, api_key=None, **kwargs):
            self.messages = self

        async def create(self, **kwargs):
            block = SimpleNamespace(type="text", text="caption")
            return SimpleNamespace(content=[block])

        async def close(self):
            raise RuntimeError("cleanup failed")

    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", BadClose)
    assert asyncio.run(captions.generate_caption("video", "topic")) == "caption"

    class BadRequest(BadClose):
        async def create(self, **kwargs):
            raise ValueError("request failed")

    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", BadRequest)
    with pytest.raises(ValueError, match="request failed"):
        asyncio.run(captions.generate_caption("video", "topic"))


# --- Claude Haiku 5.5 (RiceSuite #75) ----------------------------------------


def _sdk_reply(*blocks, stop_reason="end_turn"):
    from anthropic.types import Message, Usage

    return Message(
        id="msg_fake",
        type="message",
        role="assistant",
        model="fake",
        content=list(blocks),
        stop_reason=stop_reason,
        stop_sequence=None,
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def _thinking():
    from anthropic.types import ThinkingBlock

    return ThinkingBlock(type="thinking", thinking="", signature="sig")


def _text(value):
    from anthropic.types import TextBlock

    return TextBlock(type="text", text=value)


def _client_returning(response, calls):
    class Client:
        def __init__(self, *, api_key=None, **kwargs):
            self.messages = self

        async def create(self, **kwargs):
            calls.append(kwargs)
            return response

        async def close(self):
            pass

    return Client


def test_captions_use_claude_haiku_5_5_with_room_to_think(monkeypatch):
    monkeypatch.setattr(captions, "ANTHROPIC_API_KEY", SecretStr("fake-key"))
    calls = []
    client = _client_returning(_sdk_reply(_text("caption")), calls)
    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", client)
    asyncio.run(captions.generate_caption("video", "topic"))
    assert calls[0]["model"] == "claude-haiku-5-5"
    assert calls[0]["max_tokens"] >= 2048


def test_a_thinking_first_reply_still_returns_the_caption(monkeypatch):
    # Haiku 5.5 thinks by default: the reply can start with a thinking block.
    monkeypatch.setattr(captions, "ANTHROPIC_API_KEY", SecretStr("fake-key"))
    reply = _sdk_reply(_thinking(), _text("  a fine caption  "))
    client = _client_returning(reply, [])
    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", client)
    assert asyncio.run(captions.generate_caption("video", "topic")) == "a fine caption"


def test_a_reply_with_no_text_names_its_stop_reason(monkeypatch):
    monkeypatch.setattr(captions, "ANTHROPIC_API_KEY", SecretStr("fake-key"))
    reply = _sdk_reply(_thinking(), stop_reason="max_tokens")
    client = _client_returning(reply, [])
    monkeypatch.setattr(captions.anthropic, "AsyncAnthropic", client)
    with pytest.raises(captions.CaptionReplyError, match="max_tokens"):
        asyncio.run(captions.generate_caption("video", "topic"))
