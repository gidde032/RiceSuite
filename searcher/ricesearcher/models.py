"""Core data model (SPEC §6).

Phase 1 defines the source + transcript backbone; the full candidate-slice
schema (score, dedup, window) lands with Phase 2/3. Fields are kept explicit so
the SQLite schema in ``library/store.py`` maps one-to-one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SourceKind(StrEnum):
    YOUTUBE = "youtube"
    LOCAL = "local"


@dataclass(frozen=True)
class TranscriptWord:
    """One word with timing pinned to the source timeline, in seconds."""

    text: str
    start: float
    end: float


@dataclass
class Source:
    """An acquired piece of source material (SPEC §6 provenance fields)."""

    id: str  # stable id (content hash of the media)
    kind: SourceKind
    ref: str  # URL or local file path
    media_path: str  # path inside the content-addressed cache
    title: str = ""
    channel: str = ""
    published_at: str = ""  # ISO8601 or "" when unknown
    acquired_at: str = ""  # ISO8601Z
    duration_s: float = 0.0
    # Word-level transcript (empty until transcribed). Not stored inline in
    # SQLite as words; persisted as a child table / JSON by the store.
    words: list[TranscriptWord] = field(default_factory=list)

    @property
    def transcript_text(self) -> str:
        """Plain-text transcript joined from words."""
        return " ".join(w.text for w in self.words)
