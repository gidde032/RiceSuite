"""faster-whisper wrapper producing word-level transcripts (SPEC.md D9).

Local, free, private, word timestamps built in. The model is loaded lazily and
cached so the first request pays the load cost and later ones reuse it. Model
size / device / compute type are overridable via environment variables for
tuning without code changes.
"""

from __future__ import annotations

import gc
import os
import threading

# Prevent the HuggingFace tokenizers Rust threadpool from spawning semaphores
# that leak across a fork (uvicorn --reload). Must be set before
# faster-whisper/tokenizers is imported.
os.environ["TOKENIZERS_PARALLELISM"] = "false"

from app.models import Word

_MODEL_SIZE = os.environ.get("RICECLIPPER_WHISPER_MODEL", "small")
_DEVICE = os.environ.get("RICECLIPPER_WHISPER_DEVICE", "cpu")
_COMPUTE_TYPE = os.environ.get("RICECLIPPER_WHISPER_COMPUTE", "int8")
_cached_model = None
_model_lifecycle_lock = threading.RLock()
_progress_lock = threading.RLock()


def _configure_progress_lock() -> None:
    """Use a thread lock so tqdm does not create a tracked process semaphore."""
    from tqdm import tqdm

    tqdm.set_lock(_progress_lock)


def _default_cpu_threads() -> int:
    """Return a conservative CPU thread count for the Whisper backend."""
    default = max(1, (os.cpu_count() or 1) // 2)
    override = os.environ.get("RICECLIPPER_WHISPER_CPU_THREADS")
    if override is None:
        return default

    try:
        configured = int(override)
    except (TypeError, ValueError):
        return default
    return configured if configured > 0 else default


def _create_model():
    # Imported lazily so the module (and the rest of the app) load without the
    # heavy dependency present until transcription is actually invoked.
    from faster_whisper import WhisperModel

    # faster-whisper constructs tqdm even when log_progress=False. tqdm's
    # default lock includes multiprocessing.RLock, which is unnecessary for
    # RiceClipper and can be reported as leaked during reload shutdown.
    _configure_progress_lock()

    return WhisperModel(
        _MODEL_SIZE,
        device=_DEVICE,
        compute_type=_COMPUTE_TYPE,
        cpu_threads=_default_cpu_threads(),
    )


def _model():
    """Return the cached model, constructing it once under the lifecycle lock."""
    global _cached_model

    with _model_lifecycle_lock:
        if _cached_model is None:
            _cached_model = _create_model()
        return _cached_model


def dispose() -> None:
    """Release the cached model (ctranslate2 resources) for a clean shutdown."""
    global _cached_model

    with _model_lifecycle_lock:
        _cached_model = None
        gc.collect()


def transcribe(path: str) -> list[Word]:
    """Transcribe ``path`` into word-level tokens with second timestamps."""
    # faster-whisper returns a lazy segment iterator. Keep the lifecycle lock
    # through iteration so dispose() cannot clear the model mid-inference.
    with _model_lifecycle_lock:
        segments, _info = _model().transcribe(path, word_timestamps=True)

        words: list[Word] = []
        for segment in segments:
            for w in segment.words or []:
                text = (w.word or "").strip()
                if not text:
                    continue
                words.append(Word(text=text, start=float(w.start), end=float(w.end)))
        return words
