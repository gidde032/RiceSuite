"""Pydantic data contracts shared across the RiceClipper pipeline.

These are the wire types for the review UI and the render orchestrator. The
``Word`` model is the backbone (SPEC.md D3): word-level text pinned to the clip
timeline in seconds. Per D4, the review gate edits word *text* only — timing
stays locked to the detected boundaries.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CaptionStyle = Literal[
    "classic",
    "clean",
    "punch",
    "friendly",
    "sunset",
    "mono",
    "editorial",
    "lyric_block",
    "velvet_serif",
    "din_condensed",
    "baskerville",
]
HeaderStyle = Literal["plain", "black_plate", "white_plate"]


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
    filename: str | None = None


class RenderRequest(BaseModel):
    """The human-in-the-loop render payload from the review gate."""

    words: list[Word] = Field(default_factory=list)
    header: str = ""
    captions_on: bool = True
    caption_style: CaptionStyle = "classic"
    header_style: HeaderStyle = "plain"
    music: MusicSettings = Field(default_factory=MusicSettings)


class HandoffClip(BaseModel):
    """One rendered clip to write into the RicePoster handoff (SPEC §7 Wave-1 #1).

    ``transcript`` is the reviewed spoken text (the client already holds the
    edited words); it grounds RicePoster's caption generation. ``position`` is
    the only routing signal — RicePoster maps it to a slot on pickup.
    """

    job_id: str
    position: int = Field(ge=1)
    transcript: str = ""
    header: str = ""
    caption_style: CaptionStyle = "classic"
    header_style: HeaderStyle = "plain"


class HandoffRequest(BaseModel):
    """Client-assembled batch for POST /api/handoff."""

    clips: list[HandoffClip] = Field(default_factory=list)


class JobState(BaseModel):
    """Server-side state for one clip, surfaced to the UI."""

    id: str
    status: Literal["transcribing", "ready", "rendering", "done", "error"]
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    has_audio: bool = False
    words: list[Word] = Field(default_factory=list)
    error: str | None = None
    # True once an output mp4 exists for download.
    has_output: bool = False
