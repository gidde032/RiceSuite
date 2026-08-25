"""Pydantic data contracts shared across the RiceClipper pipeline.

These are the wire types for the review UI and the render orchestrator. The
``Word`` model is the backbone (SPEC.md D3): word-level text pinned to the clip
timeline in seconds. Per D4, the review gate edits word *text* only — timing
stays locked to the detected boundaries.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Word(BaseModel):
    """A single transcript token with locked timing (seconds)."""

    text: str
    start: float
    end: float


class MusicSettings(BaseModel):
    """Optional added-music track (SPEC.md D13)."""

    mode: Literal["none", "replace", "mix"] = "none"
    # Gain applied to the added music (mix-under level, or replace level).
    volume: float = Field(default=0.35, ge=0.0, le=2.0)
    # Filename of a track previously uploaded to this job's work dir.
    filename: Optional[str] = None


class RenderRequest(BaseModel):
    """The human-in-the-loop render payload from the review gate."""

    words: list[Word] = Field(default_factory=list)
    header: str = ""
    captions_on: bool = True
    music: MusicSettings = Field(default_factory=MusicSettings)


class JobState(BaseModel):
    """Server-side state for one clip, surfaced to the UI."""

    id: str
    status: Literal["transcribing", "ready", "rendering", "done", "error"]
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None
    has_audio: bool = False
    words: list[Word] = Field(default_factory=list)
    error: Optional[str] = None
    # True once an output mp4 exists for download.
    has_output: bool = False
