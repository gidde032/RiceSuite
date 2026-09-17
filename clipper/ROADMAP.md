# RiceClipper — Roadmap

The `SPEC.md` governs **v1**. This document owns everything after it, so the spec
can stay stable while the roadmap churns. Ordering reflects the principle behind
v1: ship a clean, provable render chassis first, then add features on top of it.

## Immediate next step

The full v1 → Wave-1 clip pipeline is implemented, merged, and **confirmed
working end to end** — upload to RiceClipper, batch review/render, filesystem
handoff, then RicePoster "Pull from Clipper" and post. The behavior-preserving
**Slate** browser-interface polish and bounded four-preset lyric-caption
addition are also merged. The **Wave-1 auto-header** (Sonnet vision + transcript)
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
batches are reviewed and processed sequentially, with eleven caption presets
and three header treatments.

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
3. **Auto-header** — Sonnet vision agent: early-frame snapshot + transcript +
   optional user note → one-sentence hook ending in 1–2 emoji, with the manual
   header as fallback. **Implemented and merged**: `render/frame.py`,
   `app/header_gen.py`, and `POST /api/jobs/{id}/header`, with per-clip
   auto-fill + regenerate-with-guidance in the review UI. The emoji spike is
   resolved via the PNG-overlay fallback (`docs/spikes/emoji-burn-in.md`).
   Prompt styles live in gitignored `prompts/*.json` (only the neutral
   `generic-header` seed is tracked); default via `RICECLIPPER_HEADER_STYLE`.
   RiceClipper still performs no posting — this is its one outbound call and it
   generates text only.

## Wave 2 — early additions

- **User-authored caption presets and finer controls** — custom font/color
  entry, arbitrary position controls, and persistent saved presets remain
  deferred until the bounded built-in choices prove insufficient.

## Deferred — longer-term

- **Music path shipped** (ADR-002, `docs/adr/ADR-002-music-path.md`, 2026-09-16):
  a per-clip `content` control, a follow framing profile for music footage, and
  a pasted-lyric fallback aligned on whisper timings. Deferred from its review:
  detector robustness on heavily filtered frames (Issue #24), a chronological
  matcher for repeated hooks (Issue #29), and the batch-render lock (Issue #30).
- **Filler-word trimming** ("um"/"uh") — transcript-driven cuts on word
  boundaries. Harder than silence-only.
- **Active-speaker reframe + zoom** — active-speaker switching between faces and
  punch-in zoom on landscape input. The single-subject landscape crop shipped
  (ADR-001, `docs/adr/ADR-001-subject-crop.md`): landscape sources crop to a
  moving 9:16 window around one speaker, with a per-clip `geometry` control and a
  blur-pad fallback. Active-speaker switching (choosing which face to follow in a
  two-shot) and zoom stay deferred; a two-shot still frames the larger face.
- **Tier-3 animated captions** — spring/bounce motion, animated resizing boxes.
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
