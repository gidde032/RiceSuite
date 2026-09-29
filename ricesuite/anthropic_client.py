"""One bounded Anthropic client construction policy for the three pillars.

The SDK applies the timeout to each attempt and may retry twice with backoff;
60 seconds is therefore not an overall request deadline. No extra retry loop
is added here. Imports and client creation are lazy so offline paths need no
credential or network client.
"""

from __future__ import annotations

from typing import Any

TIMEOUT_SECONDS = 60.0
MAX_RETRIES = 2


def create_client(
    *,
    api_key: str | None = None,
    auth_token: str | None = None,
    asynchronous: bool = False,
) -> Any:
    import anthropic

    options: dict[str, Any] = {"timeout": TIMEOUT_SECONDS, "max_retries": MAX_RETRIES}
    if api_key is not None:
        options["api_key"] = api_key
    elif auth_token is not None:
        options["auth_token"] = auth_token
    client_type = anthropic.AsyncAnthropic if asynchronous else anthropic.Anthropic
    return client_type(**options)
