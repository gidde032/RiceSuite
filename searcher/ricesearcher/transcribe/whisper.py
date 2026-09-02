"""faster-whisper transcriber (D3), reusing RiceClipper's pattern.

``faster_whisper`` is imported lazily. Model size is configurable; word-level
timestamps are requested so extraction can trim to word boundaries.
"""

from __future__ import annotations

from pathlib import Path

from ricesearcher.models import TranscriptWord


class WhisperTranscriber:
    """Local word-level transcription via faster-whisper."""

    def __init__(self, model_size: str = "small") -> None:
        self.model_size = model_size
        self._model = None

    def _load(self):  # pragma: no cover
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(self.model_size, compute_type="int8")
        return self._model

    def transcribe(self, media_path: Path) -> list[TranscriptWord]:  # pragma: no cover
        model = self._load()
        segments, _info = model.transcribe(str(media_path), word_timestamps=True)
        words: list[TranscriptWord] = []
        for seg in segments:
            for w in seg.words or []:
                words.append(
                    TranscriptWord(text=w.word.strip(), start=w.start, end=w.end)
                )
        return words
