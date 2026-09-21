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


class SliceStatus(StrEnum):
    CANDIDATE = "candidate"
    REVIEWED = "reviewed"
    SELECTED = "selected"
    HANDED_OFF = "handed_off"
    REJECTED = "rejected"


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


@dataclass
class CandidateWindow:
    """A pre-scoring candidate span emitted by the heuristic prefilter (FR-3).

    Times are seconds on the source timeline. ``features`` holds the cheap
    heuristic signals; ``heuristic_score`` is their combined shortlist score.
    """

    source_id: str
    start: float
    end: float
    text: str
    features: dict[str, float] = field(default_factory=dict)
    heuristic_score: float = 0.0


@dataclass
class CandidateSlice:
    """A scored candidate slice in the library (SPEC §6, FR-4/FR-5).

    ``pad_in``/``pad_out`` retain the scorer's original context. The reviewable
    ``target_in``/``target_out`` become the exact export interval (ADR Q4
    amendment). Dedup fields stay null until Phase 3; ``rights_risk`` defaults
    from the source kind.
    """

    id: str
    source_id: str
    pad_in: float
    pad_out: float
    target_in: float
    target_out: float
    transcript_span: str
    score: float = 0.0
    rationale: str = ""
    heuristic_score: float = 0.0
    heuristic_features: dict[str, float] = field(default_factory=dict)
    beat_profile_version: str = ""
    profile_id: str = ""
    scorer_model: str = ""
    dup_of: str | None = None
    dup_score: float = 0.0
    dup_kind: str = ""
    rights_risk: str = "med"
    status: SliceStatus = SliceStatus.CANDIDATE
    created_at: str = ""
