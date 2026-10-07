"""Clipper's one Anthropic call site (ADR-001 fact 1).

Clipper makes exactly one kind of outbound request: a text completion from an
Anthropic Sonnet model. Two opt-in features use it, each only when the user
clicks its button in the review UI (SPEC §3):

* the header generator (``app.header_gen``, SPEC §6.2), and
* the caption emoji picker (``app.emoji_gen``, SPEC §5.1, RiceSuite #66).

Both build their prompt and parse the reply themselves; this module owns the
API key check, the model choice, the client's lifetime, and the single
``messages.create`` call. It generates text and posts nothing.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from ricesuite.anthropic_client import close_client, create_client

API_KEY_ENV = "ANTHROPIC_API_KEY"
# One model setting for both features; the name predates the emoji picker.
MODEL_ENV = "RICECLIPPER_HEADER_MODEL"
DEFAULT_MODEL = "claude-sonnet-5"


class TextConfigError(RuntimeError):
    """Missing or invalid configuration, such as no API key."""


class TextGenerationError(RuntimeError):
    """The model call failed or returned nothing usable."""


def model() -> str:
    return os.getenv(MODEL_ENV) or DEFAULT_MODEL


def _create_client(api_key: str) -> Any:
    """Build a real Anthropic client (lazily, so offline paths need no SDK)."""
    return create_client(api_key=api_key)


def generate(
    *,
    purpose: str,
    system: str,
    content: Any,
    max_tokens: int,
    n: int = 1,
    client: Any | None = None,
    create: Callable[[str], Any] | None = None,
) -> list[str]:
    """Ask the model ``n`` times and return the stripped reply texts.

    ``purpose`` names the feature ("header" or "emoji") for tests and logs.
    ``client`` is injectable for tests; without it a client is built from
    the API key with ``create`` (default ``_create_client``) and closed after.
    Raises :class:`TextConfigError` before anything is sent when no key is
    set, and :class:`TextGenerationError` when the call fails.
    """
    api_key = os.getenv(API_KEY_ENV, "").strip()
    if client is None and not api_key:
        raise TextConfigError(
            f"{API_KEY_ENV} is not set — Clipper cannot generate the {purpose}."
        )
    owns_client = client is None
    if owns_client:
        client = (create or _create_client)(api_key)
    replies: list[str] = []
    try:
        for _ in range(max(1, n)):
            response = client.messages.create(
                model=model(),
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": content}],
            )
            replies.append(response.content[0].text.strip())
    except Exception as exc:
        raise TextGenerationError(f"{purpose} generation failed") from exc
    finally:
        if owns_client:
            close_client(client)
    return replies
