# RiceClipper — v1 Specification

> Status: **Ratified and implemented** (design locked via decision-challenge
> session). The v1 hardening pass is complete on `review/v1-slice` and under
> review in draft PR #1. This document remains the source of truth for v1 scope
> and the deferred roadmap.
>
> Project: **RiceClipper** — a standalone short-form video captioning tool.
> Distinct repo/project from **RicePoster** (the posting harness).

---

## 1. Purpose

RiceClipper ingests short (**under ~1 minute**) vertical videos and outputs
post-ready clips with **burned-in, word-synced captions** and an **on-screen
header**. It is the "Path 3" of a larger clipping concept: no clip *selection*
intelligence, just a clean caption/header/export render chassis.

It is built standalone for v1 but to an output contract that lets its clips drop
straight into RicePoster later. Building the render chassis first is deliberate —
the future Paths 2 and 1 (clip extraction from longer video) sit directly on top
of it.

## 2. Scope

**In scope (v1):**
decode → transcribe (word-level) → word-highlight captions → manual on-screen
header → blur-pad any non-9:16 vertical input → optional added-music track →
export 1080×1920 H.264 — all through a local web review UI with a
human-in-the-loop gate.

**Explicitly out of scope for v1 (see §7 roadmap for when):**
clip selection/extraction (Paths 2 & 1), landscape/mixed input + reframe,
dead-space/filler trimming, auto-generated header, RicePoster integration,
caption style/position configuration, animated (Tier-3) captions, auto-ducking.

## 3. Boundary & safety note

RiceClipper **performs no posting, publishing, or network upload of content**. It
reads local video files and writes local output files. The "no live post without
explicit approval" safety rule belongs to RicePoster and remains RicePoster's
responsibility at the future integration point (§7, Wave 1). RiceClipper's only
outbound network call is the deferred header agent's API request (§6.2), which
generates text and posts nothing.

## 4. Pipeline (data flow)

1. **Ingest** — user uploads a clip via the local UI.
2. **Normalize geometry** — if exactly 9:16 (1080×1920), pass through untouched;
   otherwise **blur-pad** the same-frame fill to 1080×1920 (never crop).
3. **Transcribe** — faster-whisper produces caption text with **word-level
   timestamps**, pinned to the clip timeline in seconds.
4. **Review gate (human-in-the-loop)** — user edits transcript text (timing
   stays locked to detected boundaries), toggles captions off if desired, and
   types the header. Preview available.
5. **Render captions** — emit an ASS subtitle file; burn with ffmpeg/libass:
   phrase groups with per-word highlight synced to the timestamps.
6. **Render header** — burn the user's 1–2 line header at the top, on a
   legibility plate, cleared above the caption zone.
7. **Mix audio** — original audio passes through; if the user supplied a music
   file, apply **replace** or **mix-under** (with a volume level). Because
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

## 6. Header

### 6.1 v1 — manual
A text box in the review gate. User types a 1–2 line on-screen hook. Present on
**every** clip, captioned or silent — which is why v1 needs no hand-timing editor
for silent clips: the header is the text layer.

### 6.2 Deferred (Wave 1) — auto-generated with manual fallback
A **new, distinct** generator modeled on RicePoster's caption-writer *pattern*
but a separate artifact (RicePoster writes the post-caption *field* text;
RiceClipper's header is *burned into the frame*). Inputs: early-frame snapshot +
**transcript** (which RicePoster can't provide, since it doesn't transcribe) +
optional user description line → an Anthropic **Sonnet** vision agent with a
JSON-styled prompt tuned for a punchy ≤2-line hook. Manual fallback works exactly
like the caption override — type the header, skip the agent.

**Engine lean (recorded; finalized at build):** Sonnet, keeping the vision frame.
A local/free model was considered to match the local transcription stack and
**rejected** for v1's header: header text is language *generation* (not
transcription), so "local" means a heavy multi-GB local LLM that writes
noticeably weaker social hooks; the frame snapshot is also the only automatic
signal on a music-only clip with no transcript. The header is the single most
visible line on the clip and the API cost is cents — the spot where a strong
model earns its keep. Revisit at build if desired.

## 7. Deferred roadmap (ordered)

- **Wave 1 — fast-follow (the "first improvements" cluster):**
  1. **RicePoster integration** — outputs drop into the harness's pickup contract.
  2. **Silence-only trimming** — cut long gaps (silence detection); keep A/V sync,
     smooth jump cuts.
  3. **Auto-header** — the Sonnet vision agent above, with manual fallback.
     The emoji spike is resolved via the PNG-overlay path (§8); the feature
     remains deferred Wave-1 scope.
- **Wave 2 — early additions:**
  - Caption **style/position configuration** (Tier-1 knobs: font, color, highlight
    color, position) exposed in the UI.
- **Deferred (longer-term):**
  - Filler-word trimming ("um/uh", transcript-driven cuts).
  - Landscape / mixed input + active-speaker reframe (arguably a separate project).
  - Tier-3 animated captions (behind a deliberate render-engine decision).
  - Auto-ducking + source vocal isolation for music.
  - **Path 2** (5–10 min → clip extraction) and **Path 1** (30+ min → chunked
    extraction) — the clip-selection engine, built on this render chassis.

## 8. Resolved spike

- **Color-emoji burn-in (header-critical) — passed.** The macOS/CoreText libass
  path renders missing-glyph boxes for color emoji, so emoji headers use the
  Pillow PNG-overlay fallback documented in
  [`docs/spikes/emoji-burn-in.md`](docs/spikes/emoji-burn-in.md). Captions and
  text-only headers remain on the libass path.

## 9. Recorded defaults

- Original audio passes through untouched when no music file is added (no ducking,
  no auto-added music in v1).
- Single-clip processing (no batch) in v1.
- Future-header snapshot taken from an early frame (~1s in, or first non-black
  frame).
- Tuned for sub-minute clips; no hard length cap enforced in v1.

## 10. Tech stack

- **Language / server:** Python + FastAPI, served locally (matches RicePoster).
- **Frontend:** vanilla HTML/JS review UI.
- **Transcription:** **faster-whisper** (word-level timestamps; small/medium model,
  seconds on CPU for sub-minute clips). WhisperX only if word sync looks loose.
- **Rendering:** ffmpeg + libass (ASS subtitles), blur-pad filter, audio mix.
- **Header agent (deferred):** Anthropic Sonnet (vision), JSON-styled prompt.
- **Output:** 1080×1920, H.264 / AAC, mp4.
- Local-first throughout.

## 11. Decision log

| # | Decision | Settled as | Why |
|---|----------|-----------|-----|
| D1 | Fit | Standalone v1; output contract compatible for RicePoster drop-in | Prove the render chassis fast without coupling risk; integration is Wave-1 #1 |
| D2 | Source geometry | Vertical-only; landscape/mixed deferred | Keeps Path 3 genuinely low-difficulty; reframe rivals the cost of everything else |
| D3 | Caption source | Auto-transcribe (word-level) + manual override | Transcription is the backbone; override is cheap insurance |
| D4 | Manual override | Edit transcript text (timing locked); captions-off → header-only. Hand-timed custom body captions cut from v1 | Header already covers "text on a silent clip", so no hand-timing UI needed |
| D5 | Trimming | None in v1 | The "maybe" and the riskiest component; silence-trim is Wave-1 |
| D6 | Header (v1) | Manual 1–2 line text box, on every clip | Smallest path to end-to-end; doubles as the silent-clip text layer |
| D7 | Header (auto) | Deferred (Wave 1): Sonnet vision + transcript + optional desc, manual fallback | Most visible line; strong model earns its keep; local LLM writes weaker hooks |
| D8 | Caption style | Word-by-word highlight within ~4–5 word phrase groups; Tier-1 preset | The signature look; entirely native to libass |
| D9 | Transcription | faster-whisper, local | Free, private, word timestamps built in; fits local-first setup |
| D10 | Interface | FastAPI + vanilla HTML/JS localhost, review gate | Hosts override + header entry; matches RicePoster for easy merge |
| D11 | Styling | One Tier-1 preset v1; style/position config Wave-2; Tier-3 deferred behind engine decision | Config is a time sink; template already parameterized for cheap later exposure |
| D12 | Non-9:16 handling | Blur-pad fill | Never loses content; avoids the edge-crop failure fought in RicePoster |
| D13 | Music | Optional added audio; replace **or** mix-under toggle with volume slider; v1. Auto-ducking + vocal isolation deferred | Central to actual usage; cheap since encoding already exists; adding after sync can't affect timing |
