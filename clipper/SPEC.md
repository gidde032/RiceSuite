# RiceClipper — v1 Specification

> Status: **Ratified and implemented** (design locked via decision-challenge
> session). The v1 hardening pass and bounded visual preset follow-up are merged
> to `main`. The separately ratified **Slate** browser-interface polish and its
> bounded four-preset lyric-caption follow-up are also merged to `main`; Slate
> is governed by [`docs/design/slate-ui-spec.md`](docs/design/slate-ui-spec.md)
> and does not change v1 functionality. This document remains the source of
> truth for v1 scope and the deferred roadmap.
>
> Project: **RiceClipper** — a standalone short-form video captioning tool.
> Distinct repo/project from **RicePoster** (the posting harness).

---

## 1. Purpose

RiceClipper ingests short (**under ~1 minute**) vertical or landscape videos and outputs
post-ready clips with **burned-in, word-synced captions** and an **on-screen
header**. It is the "Path 3" of a larger clipping concept: no clip *selection*
intelligence, just a clean caption/header/export render chassis.

It was built standalone for v1 with an output contract that now lets its clips
drop straight into RicePoster through a local-filesystem handoff. Building the
render chassis first is deliberate — the future Paths 2 and 1 (clip extraction
from longer video) sit directly on top of it.

## 2. Scope

**In scope (v1):**
decode → transcribe (word-level) → word-highlight captions → manual on-screen
header → normalize geometry (pass-through 9:16; subject crop or blur-pad for
landscape, D15; music follow profile, D16) → optional pasted-lyric fallback
(D16) → optional added-music track (start point and fades, D13) →
export 1080×1920 H.264 — all through a local web review UI with a
human-in-the-loop gate. A still photo (D17) skips transcription and captions; it
takes the header and music steps, then exports the same way.

**Explicitly out of scope for the original v1 slice (see §7 for delivery status):**
clip selection/extraction (Paths 2 & 1), active-speaker switching and zoom,
dead-space/filler trimming, auto-generated header, RicePoster integration,
arbitrary caption style/position editing, animated (Tier-3) captions,
auto-ducking.

The approved post-v1 visual follow-up adds a bounded set of built-in choices:
eleven caption presets and three header treatments. It does not add a general
text editor, arbitrary font/color input, or user-authored preset persistence.

## 3. Boundary & safety note

RiceClipper **performs no posting, publishing, or network upload of content**. It
reads local video and image files and writes local output files. The "no live post without
explicit approval" safety rule belongs to RicePoster and remains RicePoster's
responsibility after it separately pulls from the implemented local handoff
(§7, Wave 1). RiceClipper's only outbound network call is the implemented header
agent's API request (§6.2), which is **opt-in** (never automatic; requires an
API key and an explicit UI action) and which generates text and posts nothing.

## 4. Pipeline (data flow)

1. **Ingest** — user uploads a clip via the local UI.
2. **Normalize geometry** — if exactly 9:16 (1080×1920), pass through untouched.
   Landscape input runs local face detection at ingest and, per clip, resolves
   `auto` / `blur_pad` / `crop`: **subject crop** slides a full-height 9:16
   window that keeps the speaker inside a central safe zone; otherwise
   **blur-pad** the same-frame fill to 1080×1920 (D12, D15,
   [ADR-001](docs/adr/ADR-001-subject-crop.md),
   [design spec](docs/design/subject-crop-spec.md)). A per-clip
   `content: speech | music` setting selects the **music follow profile**:
   no face-rate gate, hold through faceless spans, static centered window
   when no face is ever found (D16, [ADR-002](docs/adr/ADR-002-music-path.md)).
   Both profiles share the Level-5 motion policy: hold minor movement,
   interpolate ordinary corrections at 30 Hz, and snap confirmed cuts, inferred
   face jumps, and returns after track loss.
3. **Transcribe** — faster-whisper produces caption text with **word-level
   timestamps**, pinned to the clip timeline in seconds.
4. **Review gate (human-in-the-loop)** — user edits transcript text (timing
   stays locked to detected boundaries), toggles captions off if desired, and
   types the header. Preview available. Under `content: music` the user may
   paste a lyric block and align it: the lyric words replace the transcript,
   with whisper word timings used as anchors only (D16,
   [design spec](docs/design/music-path-spec.md)).
5. **Render captions** — emit an ASS subtitle file; burn with ffmpeg/libass:
   phrase groups with per-word highlight synced to the timestamps.
6. **Render header** — burn the user's 1–2 line header at the top using the
   selected compact plain-text or plate treatment, cleared above the caption
   zone.
7. **Mix audio** — original audio passes through; if the user supplied a music
   file, apply **replace** or **mix-under** (with a volume level). The music
   starts at the user's chosen point and runs for the clip length. It fades in
   over 0.5 s when that point is past 0, and fades out over the last 1 s (D13).
   Because
   caption timing is already baked to the timeline in seconds, adding music at
   this stage cannot affect sync, and the source speech transcribed in step 3 was
   never contaminated by music.
8. **Export** — 1080×1920, H.264 / AAC, mp4.

## 5. Caption rendering — the complexity tiers

Rendering is ASS subtitles burned via libass. This defines a clear complexity
ladder:

- **Tier 1 (native to libass — v1 preset lives here):** font family/size/weight,
  text color, outline + shadow, position, phrase blocks, and **per-word color
  highlight / left-to-right fill synced to audio**. The target "native TikTok /
  Opus" look is entirely Tier 1.
- **Tier 2 (libass + scripting effort — natural stretch):** a highlight *box*
  behind the active word (CapCut style), computed per-word from font metrics; a
  simple scale "pop" on word appearance.
- **Tier 3 (requires a second render engine — the real fork):** fluid
  spring/bounce motion, animated resizing boxes. Needs a frame-compositing or
  HTML-to-video renderer (MoviePy / Remotion-class). This is an engine decision,
  not a style toggle, and is consciously deferred.

**v1 preset:** phrase group of ~4–5 words, bold sans font, thick outline +
shadow for legibility on any background, per-word color highlight, lower-third
position with the header cleared above. The ASS template is parameterized from
day one so exposing font/color/highlight/position config later (§7, Wave 2) is
filling in variables, not rebuilding.

### 5.1 Bounded visual preset follow-up

The review UI exposes eleven named caption presets: **Classic** (the original
v1 treatment), **Clean**, **Punch**, **Friendly**, **Sunset**, **Mono**,
**Editorial**, **Lyric Block**, **Velvet Serif**, **Powder**, and
**Baskerville**. Each remains a Tier-1 ASS/libass combination of font, size,
outline/shadow, position, base color, and active-word highlight color. The
four approved lyric treatments are fixed combinations: Avenir Next Condensed
italic with cyan (`#00E5FF`), Bodoni 72 with red (`#FF3654`), **Powder** using
the DIN Condensed font with powder blue (`#A8C7E8`), and Baskerville with teal
(`#00A7A7`). The stable internal identifier for Powder remains
`din_condensed`.

The UI also exposes three header treatments at the same compact,
reference-matched scale: **Plain text**, **Black plate**, and **White plate**.
Plain text is the default. Since RiceSuite #65 they are quick presets that fill
in the per-clip header controls (§6.3), and every header, with or without
emoji, is drawn by Pillow (§6.3); captions stay on libass.

Caption and header style are chosen per clip, seeded from a **per-slot saved
default** rather than a universal pre-upload dropdown: each slot (the "Clip N"
ordinal that maps to the RicePoster handoff position) remembers its style in the
browser (`localStorage`, local-first), starting from the v1 Classic/Plain
defaults, and editing a clip persists that slot's default for later batches. The
header controls (§6.3) are saved per slot the same way. On the audio side, choosing a music file defaults the mode to *mix under original*
while the mode is still untouched — a convenience default that never overrides a
deliberate choice and adds no new mode (D13 unchanged). A photo card offers only
*No music* and *Add music* (replace), at full volume, because a photo has no
sound of its own (D17). Under the music controls, a **Start at** slider and a
**Play segment** button choose and preview the part of the track to use (D13).

Rerendering applies the currently selected styles. Each completed render uses a
fresh media URL for both preview and Download, and output responses are not
cached, so replacing a job's MP4 cannot leave its previous subtitles on show.

### 5.2 Slate browser-interface polish

**Slate** is the ratified visual theme for the local browser review UI. It
reorganizes the existing controls into a dark, compact editing-console layout,
uses treatment-preview radio cards for per-clip header and caption selection,
and introduces a symbol-only rice-and-shears mark. It preserves all existing
values, defaults, API contracts, rendering behavior, and workflow boundaries.

The complete visual, responsive, accessibility, asset, and non-goal contract is
owned by [`docs/design/slate-ui-spec.md`](docs/design/slate-ui-spec.md).
The ratified wide-viewport editing arrangement and its Music/Speech states are
owned by [`docs/design/editor-layout-spec.md`](docs/design/editor-layout-spec.md).

## 6. Header

### 6.1 v1 — manual
A text box in the review gate. User types a 1–2 line on-screen hook. Present on
**every** clip, captioned or silent — which is why v1 needs no hand-timing editor
for silent clips: the header is the text layer.

### 6.2 Auto-generated with manual fallback (Wave 1 — implemented)
**Status: implemented.** After transcription the header auto-fills from an early
frame snapshot + transcript via an Anthropic Sonnet vision model
(`app/header_gen.py`, `render/frame.py`, `POST /api/jobs/{id}/header`); a per-clip
**Generate** button regenerates with optional guidance, and manual entry stays
the fallback. Prompt styles live in gitignored `prompts/*.json` (only the neutral
`generic-header` seed is tracked); the default is selected by
`RICECLIPPER_HEADER_STYLE`. The design is unchanged from the note below.

A **new, distinct** generator modeled on RicePoster's caption-writer *pattern*
but a separate artifact (RicePoster writes the post-caption *field* text;
RiceClipper's header is *burned into the frame*). Inputs: early-frame snapshot +
**transcript** (which RicePoster can't provide, since it doesn't transcribe) +
optional user description line → an Anthropic **Sonnet** vision agent with a
JSON-styled prompt tuned for a punchy ≤2-line hook. Manual fallback works exactly
like the caption override — type the header, skip the agent.

**Opt-in only.** The agent is never invoked automatically. Nothing (frame or
transcript) leaves the machine after transcription on its own — the user
triggers generation explicitly with the header **✨ Generate** button, and the
call is skipped entirely when no `ANTHROPIC_API_KEY` is set. What is sent on an
explicit trigger is documented in `SECURITY.md`.

**Engine lean (recorded; finalized at build):** Sonnet, keeping the vision frame.
A local/free model was considered to match the local transcription stack and
**rejected** for v1's header: header text is language *generation* (not
transcription), so "local" means a heavy multi-GB local LLM that writes
noticeably weaker social hooks; the frame snapshot is also the only available
signal on a music-only clip with no transcript. The header is the single most
visible line on the clip and the API cost is cents — the spot where a strong
model earns its keep. Revisit at build if desired.

### 6.3 Header rendering, controls, and live preview (RiceSuite #65)

**One renderer.** Every non-empty header is drawn with Pillow to a full-frame
transparent PNG (`render/header_image.py`, on the shared text and emoji module
`render/text_image.py`) and composited after the captions with ffmpeg's
`overlay`, with or without emoji. libass draws the captions only. The Pillow
header reproduces the libass header it replaced: the font is scaled the way
libass scales an ASS `Fontsize` (ascent plus descent equals the size), lines
wrap like libass `WrapStyle: 0` inside 80 px side margins, and the block is
drawn at 4× and scaled down, so glyph advances do not add up. The installed
Pillow has no raqm (no HarfBuzz shaping); on rendered frames the default plain
header's width differs from libass by at most 2 px (0.3%). A character the
chosen font lacks is drawn with the first fallback text font that has it
(Arial Unicode or Apple Symbols on macOS; DejaVu Sans or Noto on Linux), so ★,
✓, Hangul, and kana render beside colour emoji. A symbol in the emoji ranges
that the colour-emoji font has no glyph for is drawn as text. A keycap (1️⃣) is
drawn as its plain digit, because basic layout cannot place the keycap mark.
Basic layout cannot shape or reorder text, so a header in a script that needs
shaping (Arabic, Hebrew, Indic, or Southeast Asian scripts), or with a
character no text font has, is treated as a PNG failure and takes the fallback
below.

**Fallback.** If the PNG render fails (for example, no usable font, or text
Pillow cannot lay out), the render does not fail and does not drop the header.
It burns a minimal libass header (font family, size, colour, outline, plate
colour and opacity, padding, alignment, and position; square corners and no
shadow), moved up when needed to end above the caption zone, and job state
reports why in `header_note`, which the card shows.

**Controls.** Per clip, saved per slot like the caption and header styles
(§5.1): vertical position, size, a curated font list (Arial Bold by default;
Helvetica Neue Bold, Avenir Next Heavy, Futura Bold, Impact, Arial Black, DIN
Condensed Bold, Georgia Bold; a font missing on the host falls back to the
default and is marked), text colour, outline width and colour, an optional
soft blurred shadow, a plate (none, solid, or translucent) with colour,
opacity, corner radius, and padding, alignment, and line spacing. The three
treatments are presets that fill in the controls. `RenderRequest.header_look`
carries them with Pydantic bounds; without it, the `header_style` preset
decides, as before. Header text is at most 200 characters. The renderer keeps
the drawn block inside the frame and above the caption zone: it moves the
block up when needed, and draws a block taller than that space at a smaller
size until it fits. The libass fallback header is moved up by its estimated
height in the same way.

**Live preview.** `POST /api/jobs/{id}/header-preview` returns the same PNG the
render overlays (a data URL), its drawn box, and the "face near header"
warning re-checked for that box. It writes no files. The editor asks for it
250 ms after the last change to the text or controls (a reply that a newer
request overtook is dropped) and draws it over the source preview, scaled to
the source's 9:16 frame. On a source that is not 9:16 it is drawn in the
centred 9:16 window, outlined, as an approximation of the crop; the rendered
clip shows the exact result.

**Editor arrangement (variant A, chosen by Finn on 2026-10-06).** The controls
sit in a closed **Adjust header** disclosure under the header-style cards, and
position is a slider. Choosing a style card fills in the look (colours,
outline, shadow, and plate) and keeps position, size, font, alignment, and
spacing; **Reset header** returns every control to the chosen style's values.
The look is saved per slot in the browser, with the caption and header styles
(§5.1).

**Face-near-header zone.** A crop plan keeps each face box's vertical span. The
plan's stored warning, computed at ingest before any header exists, uses the
default header's span (`header_margin_v` to `header_margin_v` + 160 px). The
preview re-checks the zone against the clip's drawn header, so the warning
follows its position and size, and a clip with no header gets no header
warning. Plans saved before #65 keep their ingest warning.

## 7. Deferred roadmap (ordered)

- **Wave 1 — fast-follow (the "first improvements" cluster):**
  1. **RicePoster integration — implemented.** Outputs drop into the harness's pickup contract.
  2. **Silence-only trimming** — cut long gaps (silence detection); keep A/V sync,
     smooth jump cuts.
  3. **Header generator — implemented (opt-in).** The Sonnet vision agent above
     runs only on an explicit UI action and retains manual entry as the
     fallback; the emoji spike is resolved via the PNG-overlay path (§8).
- **Wave 2 — early additions:**
  - Caption **style/position configuration** (Tier-1 knobs: font, color, highlight
    color, position) exposed in the UI.
- **Deferred (longer-term):**
  - Filler-word trimming ("um/uh", transcript-driven cuts).
  - Active-speaker reframe and zoom for landscape input. Single-subject crop
    is ratified (D15); multi-speaker switching stays deferred.
  - Tier-3 animated captions (behind a deliberate render-engine decision).
  - Auto-ducking + source vocal isolation for music.
  - Forced alignment (wav2vec2) for lyrics, only if the D16 anchor method is
    killed. Histogram cut detector if scene 0.2 still misses cuts.
  - **Path 2** (5–10 min → clip extraction) and **Path 1** (30+ min → chunked
    extraction) — the clip-selection engine, built on this render chassis.

## 8. Resolved spike

- **Color-emoji burn-in (header-critical) — passed.** The macOS/CoreText libass
  path renders missing-glyph boxes for color emoji, so emoji headers use the
  Pillow PNG-overlay fallback documented in
  [`docs/spikes/emoji-burn-in.md`](docs/spikes/emoji-burn-in.md). Since
  RiceSuite #65 every header takes that path (§6.3); captions remain on libass.

## 9. Recorded defaults

- Original audio passes through untouched when no music file is added (no ducking,
  no auto-added music in v1).
- **Bounded batch (queue N, review each).** The review UI accepts several clips
  in one session and shows a review card per clip; the human still edits and
  approves every clip. Transcription runs **one clip at a time** (single
  Whisper model). Renders of different jobs may run concurrently: the global
  job lock covers state changes only, and each job holds its own render lock
  while ffmpeg runs (Issue #30). This is still a review/UX affordance, not a
  throughput feature. The prior "single-clip, no batch" default is superseded;
  the per-clip human-in-the-loop gate is unchanged.
- Header position: top-center, 210 px down from the top of the 1080×1920 frame
  (~11%; `StyleConfig.header_margin_v`, was 450 px until RiceSuite #20). It is
  the default of the per-clip position control (§6.3), and the top of the
  header's first line box. The subject-crop ingest "face near header" zone is
  derived from it (`header_margin_v` to `header_margin_v` + 160 px); the editor
  re-checks the zone against the clip's own header (§6.3).
- Future-header snapshot taken from an early frame (~1s in, or first non-black
  frame).
- Tuned for sub-minute clips; no hard length cap enforced in v1.

## 10. Tech stack

- **Language / server:** Python + FastAPI, served locally (matches RicePoster).
- **Frontend:** vanilla HTML/JS review UI.
- **Transcription:** **faster-whisper** (word-level timestamps; small/medium model,
  seconds on CPU for sub-minute clips). WhisperX only if word sync looks loose.
- **Rendering:** ffmpeg + libass (ASS captions), Pillow header PNG overlay
  (§6.3), blur-pad filter, audio mix.
- **Header agent:** Anthropic Sonnet (vision), JSON-styled prompt; implemented in Wave 1.
- **Output:** 1080×1920, H.264 / AAC, mp4.
- Local-first throughout.

## 11. Decision log

| # | Decision | Settled as | Why |
|---|----------|-----------|-----|
| D1 | Fit | Standalone v1; output contract compatible for RicePoster drop-in | Prove the render chassis fast without coupling risk; integration is Wave-1 #1 |
| D2 | Source geometry | Vertical-first; **landscape accepted via single-subject crop (D15, 2026-09-14)**; active-speaker reframe deferred | Original: keep Path 3 low-difficulty. Revised: interview footage is real supply; the crop is bounded by a kill criterion |
| D3 | Caption source | Auto-transcribe (word-level) + manual override; **pasted-lyric fallback under `content: music` (D16)** | Transcription is the backbone; override is cheap insurance; sung vocals defeat whisper text, so lyrics borrow its timings |
| D4 | Manual override | Edit transcript text (timing locked); captions-off → header-only. Hand-timed custom body captions cut from v1. Lyric alignment (D16) writes ordinary words; text stays editable, timing stays locked | Header already covers "text on a silent clip", so no hand-timing UI needed |
| D5 | Trimming | None in v1 | The "maybe" and the riskiest component; silence-trim is Wave-1 |
| D6 | Header (v1) | Manual 1–2 line text box, on every clip | Smallest path to end-to-end; doubles as the silent-clip text layer |
| D7 | Header (auto) | Deferred (Wave 1): Sonnet vision + transcript + optional desc, manual fallback | Most visible line; strong model earns its keep; local LLM writes weaker hooks |
| D8 | Caption style | Word-by-word highlight within ~4–5 word phrase groups; Tier-1 preset | The signature look; entirely native to libass |
| D9 | Transcription | faster-whisper, local | Free, private, word timestamps built in; fits local-first setup |
| D10 | Interface | FastAPI + vanilla HTML/JS localhost, review gate | Hosts override + header entry; matches RicePoster for easy merge |
| D11 | Styling | One Tier-1 preset v1; style/position config Wave-2; Tier-3 deferred behind engine decision. **Amended 2026-10-06 (RiceSuite [#65](https://github.com/gidde032/RiceSuite/issues/65)):** the header gets per-clip style and position controls with a live preview, and every header is drawn by Pillow (§6.3). Caption style stays the fixed presets of §5.1 | Config is a time sink; template already parameterized for cheap later exposure. Two header renderers would need every control built twice, and they had already drifted apart |
| D12 | Non-9:16 handling | Blur-pad fill as the **fallback and explicit choice**; subject crop when detection passes (D15) | Never loses content. The RicePoster "edge-crop failure" was withdrawn 2026-07-27 (TikTok trims edges itself); the surviving rule is a safe zone for the subject |
| D13 | Music | Optional added audio; replace **or** mix-under toggle with volume slider; v1. Segment start chosen per clip with an in-browser preview; the segment runs for the clip length. Added music fades in over 0.5 s when the start is past 0 and fades out over the last 1 s (amended 2026-10-03, RiceSuite [#55](https://github.com/gidde032/RiceSuite/issues/55)). Auto-ducking + vocal isolation deferred | Central to actual usage; cheap since encoding already exists; adding after sync can't affect timing. A song's opening is rarely the part a clip needs, and a mid-song cut sounds broken without a fade |
| D14 | Browser theme | Slate: dark carbon/grey chrome, rice-grey state accents, visual per-clip preset cards, symbol-only rice-and-shears mark | Makes the daily-driver review path faster to scan without changing behavior or adding editor features |
| D15 | Subject crop | Local YuNet face detection at ingest; full-height 9:16 window with pan cap and universal strong lock: hold inside an outer 20% window-width zone, settle ordinary corrections at the inner 10% boundary, and interpolate them at 30 Hz; confirmed cuts, inferred face jumps, and track returns still snap (Level 5 ratified 2026-09-20). Face center stays inside the central 70% (tuned 2026-09-15); blur-pad when `face_rate < 0.80` or `safe_rate < 0.95`; per-clip `geometry` = `auto`/`blur_pad`/`crop`; kill criterion: fewer than 5 of 6 fixtures pass after two tuning rounds. The gate applies to the `speech` profile only (D16) | Ratified 2026-09-14, motion tuning amended 2026-09-20; [ADR-001](docs/adr/ADR-001-subject-crop.md) |
| D16 | Music path | Per-clip `content` = `speech`/`music`, not remembered per slot. Music framing profile: no face-rate gate, hold through faceless spans, snap on cuts (scene 0.2, one shared pass with scores) and face return, static centered when no face; nearest-previous face between cuts, largest after a cut (both profiles). Lyric fallback: pasted block, chronological LCS anchors on whisper timings (amended 2026-09-18, #29; was `difflib`), interpolation between anchors, character-weighted fill below 25% anchors; word-level highlight survives; no new model. Kill: framing fewer than 4 of 5 music fixtures after one tuning round; lyrics visibly off on more than 2 of 5 | Ratified 2026-09-15; [ADR-002](docs/adr/ADR-002-music-path.md) |
| D17 | Photo clips | Manual upload of PNG, JPEG, or WebP (HEIC excluded). Pillow normalizes the image once: EXIF rotation applied, transparency flattened onto black, 16-bit grayscale scaled to 8 bits, long edge capped at 2160 px; images over 60 megapixels are refused before decoding. The PNG loops at 30 fps for a chosen length of 3–60 whole seconds (default 10). Blur-pad only (passthrough at exactly 1080×1920); no captions, transcript, lyrics, or crop. Header generation reads the photo. Music is "No music" or "Add music" (rendered as replace). Handoff is unchanged: an mp4 with a blank transcript, mixed freely with video clips. Searcher pickup stays video-only | Ratified 2026-10-03, RiceSuite [#54](https://github.com/gidde032/RiceSuite/issues/54). Quote images need a header and music, not captions; a blank transcript already makes Poster caption from the frame |


## RiceSuite staged progress (Issue #21, 2026-10-01)

The [ratified feature contract](../docs/design/issue-21/feature-spec.md) and
selected B reference own the compact operation bar. The existing card controls,
slot defaults, transcript/lyrics layout, per-card status locations, and rendered
9:16 preview remain the editor contract.

One bar above intake/review shows operation, textual state, stage-specific count,
and current clip/action. Finn accepted its normal document space and a short
scroll to Download at some laptop sizes on 2026-10-01; existing slot/preview
geometry remains unchanged. Pull reports actual importing/imported boundaries;
transcription reports uploading/transcribing/ready; render-all reports music
upload/render/rendered outcomes; send reports copying/copied and whole-batch
publication. A failed render preserves other successful renders and names the
held clip. A successful Render all reads “Send the batch to Poster when
ready.” Only the Send to RicePoster click sends (RiceSuite #59). Confirmed
handoff says the batch waits in Poster’s inbox.

A render whose reply is lost is checked against job state (Issue #30). Each
render request carries a `render_id` (8–64 letters, digits, `_`, `-`). Clipper
records it when it accepts the render, in memory only, and
`GET /api/jobs/{id}` returns it with the status, error, and output it governs
(RiceSuite #49). The page accepts only its own render's completion or error;
an earlier output proves nothing. If four job reads (about 12 s) show another
render before any shows this one, the card fails like any failed render
("Clipper has no record of this render; render it again") and holds the batch
until it renders again. Job reads wait on the job lock, as a render request
does, so a request queued behind a transcription or header generation is not
counted as lost. A render that completed but
lost its reply recovers from job state, with no second request.

While the workspace holds a batch that is not sent, a line under the batch
actions names the Searcher batch waiting behind it and says it opens after this
batch is sent or started over (RiceSuite #61). The line reads the read-only
`GET /api/searcher-inbox` on the automatic-pull timer. It never pulls and does
not change the progress bar.

Pull and send accept an optional `observation_id` (8–64 letters, digits, `_`,
`-`), independent of existing custody/idempotency keys. The read-only
`GET /api/progress/{pull|send}/{observation_id}` observes only that exact attempt
without acquiring the job/custody lock. Each process retains at most 64 attempts;
terminal results are preferred for expiry. Duplicate observation ids are refused
instead of replacing an attempt. Notification, start-recording, and completion
observer errors cannot fail actual work. Existing keyed replay confirms the
earlier result without writing another batch.
If that keyed send is still running, HTTP 409 includes `send_in_progress: true`;
the retry's observation is unconfirmed without claiming publication or failure
of the earlier send. A later explicit keyed retry can confirm its result.

The browser polls at 750 ms while observation is active, stops on a terminal
outcome, and preserves its latest summary in tab-local session storage. A lost
connection freezes the known values and reads may reconnect; observation never
starts or retries a mutation. A same-process reload can reconnect to that tab’s
exact attempt, while unavailable/expired or restarted-server observation says
unknown. Existing workspace restoration and keyed retry behavior remain separate.
There is no permanent history, database table, job system, restart recovery,
new cancel action, or percentage estimate. Start over clears the summary under
the existing confirmation/busy guard. Unrelated cache, lyric, player, and
validation messages retain their own surfaces.
