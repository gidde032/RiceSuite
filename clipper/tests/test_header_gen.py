"""Unit tests for the auto-header generator (SPEC §6.2).

The Anthropic client is injected as a fake, so these run fully offline — no
network, no API key, no SDK dependency exercised.
"""

from __future__ import annotations

import pytest

from app import header_gen


class _FakeContent:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, text: str) -> None:
        self.content = [_FakeContent(text)]


class _FakeMessages:
    def __init__(self, text: str, capture: list | None) -> None:
        self._text = text
        self._capture = capture

    def create(self, **kwargs):
        if self._capture is not None:
            self._capture.append(kwargs)
        return _FakeMessage(self._text)


class FakeClient:
    def __init__(self, text: str = "Header from model 🎉", capture: list | None = None):
        self.messages = _FakeMessages(text, capture)


def test_generate_header_returns_stripped_text():
    out = header_gen.generate_header(
        "they met at a party", client=FakeClient(text="  A sweet moment 🥹  ")
    )
    assert out == "A sweet moment 🥹"


def test_frame_is_sent_as_image_first_when_thumbnail_given():
    capture: list = []
    header_gen.generate_header(
        "hi", thumbnail_b64="ZmFrZQ==", client=FakeClient(capture=capture)
    )
    content = capture[0]["messages"][0]["content"]
    assert isinstance(content, list)
    assert content[0]["type"] == "image"
    assert content[0]["source"]["data"] == "ZmFrZQ=="
    assert content[0]["source"]["media_type"] == "image/jpeg"
    assert content[1]["type"] == "text"


def test_content_is_plain_text_without_thumbnail():
    capture: list = []
    header_gen.generate_header("just text", client=FakeClient(capture=capture))
    assert isinstance(capture[0]["messages"][0]["content"], str)


def test_feedback_and_avoid_flow_into_prompt():
    capture: list = []
    header_gen.generate_header(
        "the show",
        feedback="make it funnier",
        avoid="Old header 🎵",
        client=FakeClient(capture=capture),
    )
    prompt = capture[0]["messages"][0]["content"]
    assert "make it funnier" in prompt
    assert "Old header 🎵" in prompt


def test_no_transcript_uses_the_style_fallback_line():
    capture: list = []
    header_gen.generate_header("", client=FakeClient(capture=capture))
    assert "No transcript" in capture[0]["messages"][0]["content"]


def test_missing_api_key_raises_config_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(header_gen.HeaderConfigError):
        header_gen.generate_header("hi")  # no client injected → needs a real key


def test_unknown_style_raises_config_error():
    with pytest.raises(header_gen.HeaderConfigError):
        header_gen.generate_header("hi", style="does-not-exist", client=FakeClient())


def test_model_failure_becomes_generation_error():
    class Boom:
        class messages:
            @staticmethod
            def create(**kwargs):
                raise RuntimeError("api down")

    with pytest.raises(header_gen.HeaderGenerationError):
        header_gen.generate_header("hi", client=Boom())


def test_empty_model_output_raises_generation_error():
    with pytest.raises(header_gen.HeaderGenerationError):
        header_gen.generate_header("hi", client=FakeClient(text="   "))


def test_generate_headers_returns_requested_count():
    out = header_gen.generate_headers("hi", n=3, client=FakeClient(text="H 🎉"))
    assert out == ["H 🎉", "H 🎉", "H 🎉"]


def test_default_style_env_override(monkeypatch):
    monkeypatch.setenv("RICECLIPPER_HEADER_STYLE", "custom-x")
    assert header_gen.default_style() == "custom-x"
    monkeypatch.delenv("RICECLIPPER_HEADER_STYLE", raising=False)
    assert header_gen.default_style() == "generic-header"


def test_load_styles_includes_the_neutral_seed():
    styles = header_gen.load_styles()
    assert "generic-header" in styles
    assert styles["generic-header"].system_prompt
