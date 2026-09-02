"""Transcriber protocol."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ricesearcher.models import TranscriptWord


class Transcriber(Protocol):
    """Turns a media file into word-level transcript tokens."""

    def transcribe(self, media_path: Path) -> list[TranscriptWord]:
        """Return word-level tokens with timings in seconds."""
        ...
