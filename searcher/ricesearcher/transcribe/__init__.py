"""Transcription layer (SPEC §8, D3): RiceSearcher owns it via faster-whisper.

The faster-whisper adapter imports its library lazily so the core + tests run
without the model stack. Word-level timestamps are pinned to the source timeline
in seconds — the backbone the extraction scorer (Phase 2) trims against.
"""

from ricesearcher.transcribe.base import Transcriber

__all__ = ["Transcriber"]
