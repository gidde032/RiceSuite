"""Auto-header generator: frame snapshot + transcript -> a one-line hook.

The deferred Wave-1 auto-header (SPEC.md §6.2, D7): an Anthropic Sonnet vision
model turns an early frame + the reviewed transcript (+ an optional editor note)
into a single on-screen header ending in one or two emoji. Manual entry stays the
fallback — the review UI never blocks on this call.

This is the design's ONLY outbound network call (SPEC.md §3). It generates text
and posts nothing. Prompt *styles* live in ``prompts/*.json`` (mirroring
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

PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

_DEFAULT_STYLE = "generic-header"
_DEFAULT_MODEL = "claude-sonnet-5"
_STYLE_ENV = "RICECLIPPER_HEADER_STYLE"
_MODEL_ENV = "RICECLIPPER_HEADER_MODEL"
_API_KEY_ENV = "ANTHROPIC_API_KEY"

# The header sits on the request path of an interactive review UI; a ten-minute
# SDK default read timeout would be indistinguishable from a hang.
_API_TIMEOUT_S = 60.0
_MAX_TOKENS = 200


class HeaderConfigError(RuntimeError):
    """Missing/invalid configuration (no API key, unknown style)."""


class HeaderGenerationError(RuntimeError):
    """The model call failed or returned nothing usable."""


class HeaderStyle(BaseModel):
    name: str
    display_name: str
    system_prompt: str
    no_topic_fallback: str


def default_style() -> str:
    return os.getenv(_STYLE_ENV) or _DEFAULT_STYLE


def _model() -> str:
    return os.getenv(_MODEL_ENV) or _DEFAULT_MODEL


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
    """Build a real Anthropic client. Imported lazily so the module loads (and
    tests that inject a fake client run) without the SDK installed."""
    import anthropic

    return anthropic.Anthropic(api_key=api_key, timeout=_API_TIMEOUT_S)


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
    api_key = os.getenv(_API_KEY_ENV, "").strip()
    if client is None and not api_key:
        raise HeaderConfigError(
            f"{_API_KEY_ENV} is not set — the auto-header cannot be generated."
        )

    styles = load_styles()
    chosen = style or default_style()
    selected = styles.get(chosen)
    if selected is None:
        raise HeaderConfigError(
            f"Unknown header style {chosen!r}. "
            f"Available: {', '.join(sorted(styles)) or 'none'}"
        )

    if client is None:
        client = _create_client(api_key)

    user_prompt = _build_user_prompt(
        transcript, selected.no_topic_fallback, note, feedback, avoid
    )
    content = _build_content(user_prompt, thumbnail_b64)

    headers: list[str] = []
    try:
        for _ in range(max(1, n)):
            response = client.messages.create(
                model=_model(),
                max_tokens=_MAX_TOKENS,
                system=selected.system_prompt,
                messages=[{"role": "user", "content": content}],
            )
            headers.append(response.content[0].text.strip())
    except HeaderGenerationError:
        raise
    except Exception as exc:
        raise HeaderGenerationError("header generation failed") from exc

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
