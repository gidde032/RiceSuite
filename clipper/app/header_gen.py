"""Auto-header generator: frame snapshot + transcript -> a one-line hook.

The deferred Wave-1 auto-header (SPEC.md §6.2, D7): an Anthropic Sonnet vision
model turns an early frame + the reviewed transcript (+ an optional editor note)
into a single on-screen header ending in one or two emoji. Manual entry stays the
fallback — the review UI never blocks on this call.

It shares Clipper's one Anthropic call site, ``app.anthropic_text``, with the
caption emoji picker (SPEC.md §3, ADR-001 fact 1). It generates text and posts
nothing. Prompt *styles* live in ``prompts/*.json`` (mirroring
RicePoster's caption styles); only the neutral ``generic-header`` seed is tracked,
so a maintainer-specific style naming real people stays local and gitignored.

Extensibility: the core returns a list of candidate headers (length 1 today) so a
future "give me 2-3 options" UI is a caller change, not a rewrite here.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app import anthropic_text

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_DEFAULT_STYLE = "generic-header"
_STYLE_ENV = "RICECLIPPER_HEADER_STYLE"

# The header sits on the request path of an interactive review UI; a ten-minute
# SDK default read timeout would be indistinguishable from a hang.
_MAX_TOKENS = 200


# Missing/invalid configuration (no API key, unknown style), and a failed or
# empty model call. The shared call site raises the same types.
HeaderConfigError = anthropic_text.TextConfigError
HeaderGenerationError = anthropic_text.TextGenerationError


class HeaderStyle(BaseModel):
    name: str
    display_name: str
    system_prompt: str
    no_topic_fallback: str


def default_style() -> str:
    return os.getenv(_STYLE_ENV) or _DEFAULT_STYLE


def load_styles() -> dict[str, HeaderStyle]:
    """Load every header style from ``prompts/``. A malformed file is skipped so
    one bad local edit can't take down generation for the neutral seed too."""
    styles: dict[str, HeaderStyle] = {}
    for path in sorted(PROMPTS_DIR.glob("*.json")):
        try:
            style = HeaderStyle(**json.loads(path.read_text(encoding="utf-8")))
        except Exception:
            continue
        styles[style.name] = style
    return styles


def _build_user_prompt(
    transcript: str,
    no_topic_fallback: str,
    note: str = "",
    feedback: str = "",
    avoid: str = "",
) -> str:
    parts = ["Write the on-screen header for this short vertical clip."]
    if transcript.strip():
        parts.append(f"Transcript: {transcript.strip()}")
    else:
        parts.append(no_topic_fallback)
    if note.strip():
        parts.append(f"Extra context from the editor: {note.strip()}")
    if avoid.strip():
        parts.append(
            f'A previous header was: "{avoid.strip()}" '
            "Write a meaningfully different one: different angle and phrasing."
        )
    if feedback.strip():
        parts.append(f"Revision guidance from the user: {feedback.strip()}")
    return " ".join(parts)


def _build_content(user_prompt: str, thumbnail_b64: str = "") -> Any:
    """Message content. With a frame (base64 JPEG, no data-URL prefix) the image
    goes first so the model grounds the header in what the clip shows."""
    if not thumbnail_b64:
        return user_prompt
    return [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/jpeg",
                "data": thumbnail_b64,
            },
        },
        {
            "type": "text",
            "text": (
                f"{user_prompt} The attached image is an early frame from the "
                "clip — ground the header in what it actually shows."
            ),
        },
    ]


def _create_client(api_key: str) -> Any:
    """Build a real Anthropic client (tests replace this)."""
    return anthropic_text._create_client(api_key)


def generate_headers(
    transcript: str,
    *,
    thumbnail_b64: str = "",
    note: str = "",
    feedback: str = "",
    avoid: str = "",
    style: str | None = None,
    n: int = 1,
    client: Any | None = None,
) -> list[str]:
    """Generate ``n`` candidate headers. Returns a list (length ``n``).

    ``client`` is injectable for tests; production builds one from the API key.
    """
    if client is None and not os.getenv(anthropic_text.API_KEY_ENV, "").strip():
        raise HeaderConfigError(
            f"{anthropic_text.API_KEY_ENV} is not set — the auto-header cannot "
            "be generated."
        )

    styles = load_styles()
    chosen = style or default_style()
    selected = styles.get(chosen)
    if selected is None:
        raise HeaderConfigError(
            f"Unknown header style {chosen!r}. "
            f"Available: {', '.join(sorted(styles)) or 'none'}"
        )

    user_prompt = _build_user_prompt(
        transcript, selected.no_topic_fallback, note, feedback, avoid
    )
    headers = anthropic_text.generate(
        purpose="header",
        system=selected.system_prompt,
        content=_build_content(user_prompt, thumbnail_b64),
        max_tokens=_MAX_TOKENS,
        n=n,
        client=client,
        create=_create_client,
    )

    if not any(headers):
        raise HeaderGenerationError("model returned an empty header")
    return headers


def generate_header(
    transcript: str,
    *,
    thumbnail_b64: str = "",
    note: str = "",
    feedback: str = "",
    avoid: str = "",
    style: str | None = None,
    client: Any | None = None,
) -> str:
    """Single-header convenience over :func:`generate_headers`."""
    return generate_headers(
        transcript,
        thumbnail_b64=thumbnail_b64,
        note=note,
        feedback=feedback,
        avoid=avoid,
        style=style,
        client=client,
    )[0]
