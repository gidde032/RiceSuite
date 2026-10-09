"""Pydantic data contracts shared across the RiceClipper pipeline.

These are the wire types for the review UI and the render orchestrator. The
``Word`` model is the backbone (SPEC.md D3): word-level text pinned to the clip
timeline in seconds. Per D4, the review gate edits word *text* only — timing
stays locked to the detected boundaries.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field

from render.ass import DEFAULT_CAPTION_STYLE, RETIRED_CAPTION_STYLES
from render.text_image import is_emoji_cluster


def _retire_caption_style(value: object) -> object:
    """A caption style cut in RiceSuite #79 takes the default style."""
    return DEFAULT_CAPTION_STYLE if value in RETIRED_CAPTION_STYLES else value


# Matches ``render.ass.CAPTION_STYLE_NAMES``.
CaptionStyle = Annotated[
    Literal[
        "montserrat",
        "montserrat_violet",
        "montserrat_sky",
        "montserrat_green",
        "punch",
        "punch_volt",
        "punch_red",
        "punch_blue",
        "pop",
        "pop_bubblegum",
        "pop_lime",
        "pop_fire",
        "neon",
        "neon_inverse",
        "friendly",
        "editorial",
        "lyric_block",
        "velvet_serif",
        "din_condensed",
    ],
    BeforeValidator(_retire_caption_style),
]
HeaderStyle = Literal["plain", "black_plate", "white_plate"]
# The curated header fonts; keys of ``render.text_image.FONT_CHOICES``.
HeaderFont = Literal[
    "arial", "helvetica", "avenir", "futura", "impact", "arial_black", "din", "georgia"
]
HexColor = Annotated[str, Field(pattern=r"^[0-9A-Fa-f]{6}$"), AfterValidator(str.upper)]
# Longest header the review gate accepts. A hook is one or two short lines.
HEADER_MAX_CHARS = 200
# Header top bounds, in output px. The renderer also keeps the whole drawn
# block inside the frame and above the caption zone (RiceSuite #65).
HEADER_Y_MIN = 0
HEADER_Y_MAX = 1380
# Per-clip framing choice (ADR-001). "auto" follows the plan decision; "crop"
# and "blur_pad" override it.
Geometry = Literal["auto", "blur_pad", "crop"]
JobKind = Literal["video", "photo"]
# A photo clip's length bounds, in whole seconds (Issue #54, SPEC.md D17).
PHOTO_MIN_SECONDS = 3
PHOTO_MAX_SECONDS = 60


class Word(BaseModel):
    """A single transcript token with locked timing (seconds)."""

    text: str
    start: float
    end: float
    line_start: bool = False


def _emoji_cluster(value: str) -> str:
    if not is_emoji_cluster(value):
        raise ValueError("must be one emoji")
    return value


EmojiCluster = Annotated[str, AfterValidator(_emoji_cluster)]
# Upper bound on emoji picks in one request: far more than any clip's phrases.
EMOJI_PICKS_MAX = 500


class EmojiPick(BaseModel):
    """One phrase's emoji, anchored to a word (RiceSuite #66).

    ``word`` is the global index of the anchor word in the clip's word list,
    so a text edit keeps the pick (timing is locked, D4). The phrase that
    contains the word shows the row; the first anchor in a phrase wins.
    """

    model_config = ConfigDict(extra="forbid")

    word: int = Field(ge=0)
    emoji: list[EmojiCluster] = Field(min_length=1, max_length=2)


# The emoji picker's prompt numbers every word; this bounds it. A long clip at
# speaking pace is well under a few thousand words.
WORDS_MAX = 5000


class EmojiRequest(BaseModel):
    """Body of ``POST /api/jobs/{id}/emoji``: the reviewed words to pick for."""

    words: list[Word] = Field(default_factory=list, max_length=WORDS_MAX)


class LyricsRequest(BaseModel):
    """Pasted lyric text for alignment against reference timings."""

    lyrics: str


class LyricsResult(BaseModel):
    """Alignment result returned by the lyrics endpoint."""

    words: list[Word]
    anchor_rate: float
    method: Literal["anchors", "even_fill"]
    # Largest distance (seconds) any matched anchor's delivered start had to move
    # from its reference timing to satisfy the MIN_WORD_S / in-clip invariant.
    # Diagnostic only — timing behavior is unchanged (A1 option 1).
    anchor_drift: float = 0.0
    # True when anchor_drift exceeds the tolerance bar: the UI surfaces a
    # "timing approximate" signal so a large, rare shift is visible.
    anchor_drift_warning: bool = False


# Upper bound on a music segment start: 24 hours, far past any real track.
MUSIC_START_MAX = 86400.0


class MusicSettings(BaseModel):
    """Optional added-music track (SPEC.md D13)."""

    mode: Literal["none", "replace", "mix"] = "none"
    # Gain applied to the added music (mix-under level, or replace level).
    volume: float = Field(default=0.35, ge=0.0, le=2.0)
    # Filename of a track previously uploaded to this job's work dir.
    filename: str | None = None
    # Where the segment starts in the track, in seconds (Issue #55). The
    # segment runs for the clip's length.
    start: float = Field(default=0.0, ge=0.0, le=MUSIC_START_MAX, allow_inf_nan=False)


class HeaderLook(BaseModel):
    """Per-clip header controls (RiceSuite #65).

    The defaults are the Plain preset at the RiceSuite #20 position. Sizes are
    in output px of the 1080x1920 frame; ``size`` is the line height.
    """

    model_config = ConfigDict(extra="forbid")

    font: HeaderFont = "arial"
    size: int = Field(default=42, ge=24, le=96)
    y: int = Field(default=210, ge=HEADER_Y_MIN, le=HEADER_Y_MAX)
    color: HexColor = "FFFFFF"
    outline: int = Field(default=2, ge=0, le=8)
    outline_color: HexColor = "000000"
    shadow: bool = False
    plate: Literal["none", "solid", "translucent"] = "none"
    plate_color: HexColor = "000000"
    plate_opacity: int = Field(default=75, ge=10, le=100)
    plate_radius: int = Field(default=0, ge=0, le=40)
    plate_padding: int = Field(default=16, ge=0, le=40)
    align: Literal["left", "center", "right"] = "center"
    line_spacing: float = Field(default=1.0, ge=0.8, le=2.0, allow_inf_nan=False)


class HeaderFields(BaseModel):
    """The header text and look, shared by render and preview requests.

    ``header_look`` carries the controls. Without it, the ``header_style``
    preset decides the look, as before RiceSuite #65.
    """

    header: str = Field(default="", max_length=HEADER_MAX_CHARS)
    header_style: HeaderStyle = "plain"
    header_look: HeaderLook | None = None


class HeaderPreviewRequest(HeaderFields):
    """Body of ``POST /api/jobs/{id}/header-preview``."""

    # Header warnings depend on the geometry the render will use, including
    # whether auto resolves to the crop plan's crop or blur-pad decision.
    geometry: Geometry = "auto"
    # The clip shows caption emoji rows (RiceSuite #66): the header keeps
    # above the wider caption band, and the face check uses it.
    emoji_rows: bool = False


class RenderRequest(HeaderFields):
    """The human-in-the-loop render payload from the review gate."""

    words: list[Word] = Field(default_factory=list)
    captions_on: bool = True
    caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE
    # Caption pop, active-word bump, and soft shadow (RiceSuite #66). Off
    # renders the captions exactly as before.
    motion: bool = True
    # Caption emoji rows (RiceSuite #66): off by default. ``emoji`` holds the
    # reviewed picks; they are drawn only while ``emoji_on`` is set.
    emoji_on: bool = False
    emoji: list[EmojiPick] = Field(default_factory=list, max_length=EMOJI_PICKS_MAX)
    geometry: Geometry = "auto"
    content: Content = "speech"
    music: MusicSettings = Field(default_factory=MusicSettings)
    # Clip length for a photo job. A video job ignores it.
    photo_duration: int = Field(default=10, ge=PHOTO_MIN_SECONDS, le=PHOTO_MAX_SECONDS)
    # The page names each render. Job state reports the id of the render it
    # accepted, so a page that lost the reply can tell its own render's outcome
    # from an earlier output (RiceSuite #49).
    render_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")


class HeaderRequest(BaseModel):
    """Auto-header generation request (SPEC.md §6.2, Wave-1).

    ``transcript`` is the client's reviewed text; when blank the server falls
    back to the job's transcribed words. ``avoid`` + ``feedback`` drive the
    regenerate-with-guidance path (mirrors RicePoster's caption regeneration).
    """

    transcript: str = ""
    note: str = ""
    feedback: str = ""
    avoid: str = ""
    style: str | None = None


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
    caption_style: CaptionStyle = DEFAULT_CAPTION_STYLE
    header_style: HeaderStyle = "plain"


class HandoffRequest(BaseModel):
    """Client-assembled batch for POST /api/handoff."""

    clips: list[HandoffClip] = Field(default_factory=list)
    # The page reuses a send's key when it retries a send whose reply it never
    # saw; a key that already wrote a batch gets that batch back (W1-01).
    send_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")
    observation_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{8,64}$")
    # The reviewer confirmed sending clips that already went to RicePoster.
    resend: bool = False


class JobState(BaseModel):
    """Server-side state for one clip, surfaced to the UI."""

    id: str
    status: Literal["transcribing", "ready", "rendering", "done", "error"]
    kind: JobKind = "video"
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    has_audio: bool = False
    words: list[Word] = Field(default_factory=list)
    error: str | None = None
    # True once an output mp4 exists for download.
    has_output: bool = False
    # The ``render_id`` of the render this job last accepted; the status, error
    # and output above belong to it once rendering ends (RiceSuite #49).
    render_id: str | None = None
    # Set when the last render drew the header with the libass fallback
    # because the Pillow header failed (RiceSuite #65); says why.
    header_note: str | None = None
    # Set when the last render left the caption emoji out (RiceSuite #66),
    # for example with no colour-emoji font; says why.
    emoji_note: str | None = None
    # Subject-crop framing decision (ADR-001). Only set for landscape input.
    crop_plan: CropPlan | None = None
    music_plan: CropPlan | None = None


# --- Subject crop (ADR-001) ---------------------------------------------------
# Data contracts for the subject-focused 9:16 crop. ``render.framing`` is the
# pure producer of a ``CropPlan``; ``render.subject`` (F3) produces the track.


class TrackSample(BaseModel):
    """One detected face sample, in source pixels."""

    t: float
    cx: float
    cy: float
    w: float
    h: float


class CropSample(BaseModel):
    """Resolved crop-window x for one sample. Even int, source pixels."""

    t: float
    x: int
    # True when this position must be reached immediately (scene/face jump or
    # return after loss). Ordinary moves may be interpolated at render time.
    snap: bool = False


Content = Literal["speech", "music"]

CropReason = Literal[
    "ok",
    "low_face_rate",
    "low_safe_rate",
    "no_samples",
    "analysis_failed",
    "hold_static",
]


class CropPlan(BaseModel):
    """Framing decision over a track (ADR-001). Pure output of render.framing."""

    decision: Literal["crop", "blur_pad"]
    reason: CropReason
    face_rate: float
    safe_rate: float
    window_w: int
    window_h: int
    samples: list[CropSample] = Field(default_factory=list)
    warning: Literal["header_zone", "caption_zone"] | None = None
    profile: Content = "speech"
    # (top, bottom) of each detected face box, in source px. The editor's
    # "face near header" check reuses them for the clip's own header
    # (RiceSuite #65). Empty on plans saved before that.
    face_spans: list[tuple[float, float]] = Field(default_factory=list)
    # Zero preserves pre-tuning persisted plans. New plans use 30 Hz command
    # interpolation for ordinary movement while ``CropSample.snap`` remains hard.
    interpolation_fps: int = Field(default=0, ge=0, le=120)


# JobState references CropPlan by forward reference; resolve it now that CropPlan
# is defined.
JobState.model_rebuild()
