"""Offline fakes for Clipper's one Anthropic call site, built from real SDK types.

Replies are real ``anthropic.types.Message`` objects, so a test can only pass a
shape the API can actually return: a list of typed content blocks with a
``stop_reason``. Current models think by default and their replies can start
with a ``thinking`` block (RiceSuite #66 review), which an attribute bag with a
single ``.text`` could never represent. Nothing here reaches the network.
"""

from __future__ import annotations

import anthropic
import httpx2
from anthropic.types import Message, TextBlock, ThinkingBlock, Usage


def text(value: str) -> TextBlock:
    return TextBlock(type="text", text=value)


def thinking() -> ThinkingBlock:
    """A thinking block as the API returns it by default: display omitted."""
    return ThinkingBlock(type="thinking", thinking="", signature="sig")


def reply(*blocks, stop_reason: str = "end_turn") -> Message:
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


def status_error(cls: type[anthropic.APIStatusError], status: int):
    """An SDK status error as ``messages.create`` raises it."""
    request = httpx2.Request("POST", "http://127.0.0.1/v1/messages")
    response = httpx2.Response(status, request=request)
    body = {"type": "error", "error": {"type": "stub", "message": "stub"}}
    return cls(f"Error code: {status}", response=response, body=body)


class _Messages:
    def __init__(self, result, calls: list) -> None:
        self._result = result
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class FakeClient:
    """A client whose ``messages.create`` records its kwargs.

    ``result`` is a reply text (one text block), a ``Message``, or an
    exception to raise. ``calls`` is the list the kwargs go to.
    """

    def __init__(self, result="stub reply", calls: list | None = None) -> None:
        if isinstance(result, str):
            result = reply(text(result))
        self.calls: list[dict] = [] if calls is None else calls
        self.messages = _Messages(result, self.calls)
