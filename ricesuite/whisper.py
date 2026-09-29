"""Neutral faster-whisper construction and word-level inference.

The callers own their process-local caches and transcript conversion. Importing
this module never imports the inference backend or starts a model download.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def create_model(
    model_size: str,
    *,
    compute_type: str,
    device: str | None = None,
    cpu_threads: int | None = None,
    before_create: Callable[[], None] | None = None,
) -> Any:
    from faster_whisper import WhisperModel

    if before_create is not None:
        before_create()
    options: dict[str, Any] = {"compute_type": compute_type}
    if device is not None:
        options["device"] = device
    if cpu_threads is not None:
        options["cpu_threads"] = cpu_threads
    return WhisperModel(model_size, **options)


def transcribe_words(model: Any, path: str) -> list[Any]:
    """Consume the lazy segment iterator before returning to the caller."""
    segments, _info = model.transcribe(path, word_timestamps=True)
    return [word for segment in segments for word in (segment.words or [])]
