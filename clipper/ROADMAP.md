# RiceClipper — Roadmap

The `SPEC.md` governs **v1**. This document owns everything after it, so the spec
can stay stable while the roadmap churns. Ordering reflects the principle behind
v1: ship a clean, provable render chassis first, then add features on top of it.

## Immediate next step

The full v1 → Wave-1 clip pipeline is implemented, merged, and **confirmed
working end to end** — upload to RiceClipper, batch review/render, filesystem
handoff, then RicePoster "Pull from Clipper" and post. The behavior-preserving
**Slate** browser-interface polish and bounded four-preset lyric-caption
addition are also merged. The **Wave-1 auto-header** (vision model + transcript)
is now implemented as well. The one remaining Wave-1 product addition
(silence-only trimming) stays deferred behind an explicit phase change.

## Delivered visual polish — Slate

Slate redesigns the existing local review UI around a dark carbon/grey editing
console, rice-grey interaction states, treatment-preview cards for per-clip
header and caption selection, and a symbol-only rice-and-shears mark. The Slate
UI slice preserves the existing API, render pipeline, batch semantics, and
RicePoster handoff. The bounded lyric-preset addition extends the catalog to
eleven fixed caption presets while keeping the original defaults and deferred
custom-preset boundary.

## v1 (current — see SPEC.md)

Decode → transcribe (word-level) → word-highlight captions → manual on-screen
header → subject crop or blur-pad landscape input and blur-pad non-9:16 vertical
input → optional added-music (replace / mix with volume) → export 1080×1920
H.264, through a local FastAPI review UI with a human-in-the-loop gate. Bounded
batches are reviewed and processed sequentially, with twelve caption presets
and three header treatments. A per-clip Motion toggle adds a phrase pop-in, an
active-word bump, and a soft shadow to any preset, and a per-clip Emoji toggle
adds model-suggested, hand-edited emoji rows above or below some phrases
(RiceSuite [#66](https://github.com/gidde032/RiceSuite/issues/66)). Subject crop uses the universal Level-5 strong
lock for speech and music: minor motion holds, ordinary correction interpolates
at 30 Hz, and confirmed cuts or target reacquisition remain immediate.

## Wave 1 — fast-follow (the "first improvements" cluster)

1. **RicePoster integration** — outputs drop into the harness's pickup contract
   (naming/folder layout it expects). RiceClipper still performs no posting. The
   handoff contract is ratified in
   [`docs/integration/riceposter-handoff.md`](./docs/integration/riceposter-handoff.md).
   **Implemented, merged, and verified end to end**: batch review/render in
   RiceClipper (#4), the producer-side handoff writer (#5,
   `POST /api/handoff` → `~/riceclipper-handoff/`), and the RicePoster-side
   "Pull from Clipper" pickup + auto-caption (RicePoster #77), where captions are
   generated on a real frame captured from the staged clip.
2. **Silence-only trimming** — cut long silent gaps via silence detection; keep
   A/V in sync and smooth the jump cuts.
3. **Auto-header** — vision agent (Claude Haiku 5.5 by default): early-frame snapshot + transcript +
   optional user note → one-sentence hook ending in 1–2 emoji, with the manual
   header as fallback. **Implemented and merged**: `render/frame.py`,
   `app/header_gen.py`, and `POST /api/jobs/{id}/header`, with per-clip
   auto-fill + regenerate-with-guidance in the review UI. The emoji spike is
   resolved via the PNG-overlay fallback (`docs/spikes/emoji-burn-in.md`).
   Prompt styles live in gitignored `prompts/*.json` (only the neutral
   `generic-header` seed is tracked); default via `RICECLIPPER_HEADER_STYLE`.
   RiceClipper still performs no posting — this is its one outbound call site
   and it generates text only. The caption emoji picker (RiceSuite
   [#66](https://github.com/gidde032/RiceSuite/issues/66)) shares it, opt-in on
   its own button.

## Wave 2 — early additions

- **Music waveform** (RiceSuite [#56](https://github.com/gidde032/RiceSuite/issues/56))
  — draw the track's waveform in the segment picker, so the user can see where
  to start.
- **User-authored caption presets and finer controls** — the header half
  shipped in RiceSuite [#65](https://github.com/gidde032/RiceSuite/issues/65):
  per-clip header position, size, curated font, colours, outline, shadow,
  plate, alignment, and line spacing, saved per slot, with a live preview
  (SPEC §6.3). Caption font/colour entry, caption position controls, and
  persistent saved presets remain deferred until the bounded caption presets
  prove insufficient.

## Deferred — longer-term

- **Music path shipped** (ADR-002, `docs/adr/ADR-002-music-path.md`, 2026-09-16):
  a per-clip `content` control, a follow framing profile for music footage, and
  a pasted-lyric fallback aligned on whisper timings. Deferred from its review:
  detector robustness on heavily filtered frames (Issue #24). The batch-render
  lock (Issue #30) was fixed in PR #31. The chronological matcher for repeated
  hooks (Issue #29) was fixed in PR #32.
- **Filler-word trimming** ("um"/"uh") — transcript-driven cuts on word
  boundaries. Harder than silence-only.
- **Active-speaker reframe + zoom** — active-speaker switching between faces and
  punch-in zoom on landscape input. The single-subject landscape crop shipped
  (ADR-001, `docs/adr/ADR-001-subject-crop.md`): landscape sources crop to a
  moving 9:16 window around one speaker, with a per-clip `geometry` control and a
  blur-pad fallback. Active-speaker switching (choosing which face to follow in a
  two-shot) and zoom stay deferred; a two-shot still frames the larger face.
  The 2026-09-20 motion-tuning amendment applies the Level-5 strong lock to both
  speech and music profiles without changing target selection.
- **Tier-3 animated captions** — spring/bounce motion, animated resizing boxes.
  The Tier-2 pop-in, active-word bump, and soft shadow shipped on libass in
  RiceSuite [#66](https://github.com/gidde032/RiceSuite/issues/66) (SPEC §5).
  Requires adopting a second render engine (compositing / HTML-to-video). This is
  a deliberate engine decision, not a style toggle.
- **Auto-ducking + vocal isolation** — music automatically dips under speech;
  isolate vocals from a music-laden source. Beyond v1's fixed-level mix.
- **Forced lyric alignment** (wav2vec2) — only if the ADR-002 anchor method
  is killed. **Histogram cut detector** — only if scene 0.2 still misses cuts
  on filtered footage.
- **Path 2** — 5–10 min input → LLM clip extraction (single-context) on this
  render chassis.
- **Path 1** — 30+ min input → chunked extraction with global re-ranking. The
  full Opus-Clip problem.


## RiceSuite progress update — 2026-10-01

Issue #21 adds B’s compact staged bar to pull/transcribe and render/send, using
actual coarse operation boundaries. Existing editor slots and the rendered
preview design remain the reference. Permanent run history and server-restart
observation recovery remain outside this increment; reconsider only with
ordinary-use evidence. See SPEC’s RiceSuite staged progress section.
