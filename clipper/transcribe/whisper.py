"""faster-whisper wrapper producing word-level transcripts (SPEC.md D9).

Local, free, private, word timestamps built in. The model is loaded lazily and
cached so the first request pays the load cost and later ones reuse it. Model
size / device / compute type are overridable via environment variables for
tuning without code changes.
"""

from __future__ import annotations

import gc
import os
from functools import lru_cache

# Prevent the HuggingFace tokenizers Rust threadpool from spawning semaphores
# that leak across a fork (uvicorn --reload) — the usual source of the
# "resource_tracker: leaked semaphore objects" warning at shutdown. Must be set
# before faster-whisper/tokenizers is imported.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from app.models import Word

_MODEL_SIZE = os.environ.get("RICECLIPPER_WHISPER_MODEL", "small")
_DEVICE = os.environ.get("RICECLIPPER_WHISPER_DEVICE", "cpu")
_COMPUTE_TYPE = os.environ.get("RICECLIPPER_WHISPER_COMPUTE", "int8")


@lru_cache(maxsize=1)
def _model():
    # Imported lazily so the module (and the rest of the app) load without the
    # heavy dependency present until transcription is actually invoked.
    from faster_whisper import WhisperModel

    return WhisperModel(_MODEL_SIZE, device=_DEVICE, compute_type=_COMPUTE_TYPE)


def dispose() -> None:
    """Release the cached model (ctranslate2 resources) for a clean shutdown."""
    _model.cache_clear()
    gc.collect()


def transcribe(path: str) -> list[Word]:
    """Transcribe ``path`` into word-level tokens with second timestamps."""
    segments, _info = _model().transcribe(path, word_timestamps=True)

    words: list[Word] = []
    for segment in segments:
        for w in segment.words or []:
            text = (w.word or "").strip()
            if not text:
                continue
            words.append(Word(text=text, start=float(w.start), end=float(w.end)))
    return words
