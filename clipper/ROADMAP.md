# RiceClipper — Roadmap

The `SPEC.md` governs **v1**. This document owns everything after it, so the spec
can stay stable while the roadmap churns. Ordering reflects the principle behind
v1: ship a clean, provable render chassis first, then add features on top of it.

## Immediate next step

The full v1 → Wave-1 clip pipeline is implemented and **confirmed working end to
end** — upload to RiceClipper, batch review/render, filesystem handoff, then
RicePoster "Pull from Clipper" and post. It lives in draft PRs pending merge
(#4 batch review/render, #5 handoff writer, RicePoster #77 pickup). Next is
merging those, then the remaining Wave-1 items (silence-only trimming,
auto-header). Earlier work already merged to `main`: v1 slice, hardening pass,
and the bounded visual presets (seven caption presets, three header treatments,
color-emoji via the Pillow overlay).

## v1 (current — see SPEC.md)

Decode → transcribe (word-level) → word-highlight captions → manual on-screen
header → blur-pad non-9:16 vertical input → optional added-music (replace / mix
with volume) → export 1080×1920 H.264, through a local FastAPI review UI with a
human-in-the-loop gate. One clip at a time. Single Tier-1 caption preset.

## Wave 1 — fast-follow (the "first improvements" cluster)

1. **RicePoster integration** — outputs drop into the harness's pickup contract
   (naming/folder layout it expects). RiceClipper still performs no posting. The
   handoff contract is ratified in
   [`docs/integration/riceposter-handoff.md`](./docs/integration/riceposter-handoff.md).
   **Implemented and verified end to end** (pending PR merge): batch
   review/render in RiceClipper (#4), the producer-side handoff writer (#5,
   `POST /api/handoff` → `~/riceclipper-handoff/`), and the RicePoster-side
   "Pull from Clipper" pickup + auto-caption (RicePoster #77), where captions are
   generated on a real frame captured from the staged clip.
2. **Silence-only trimming** — cut long silent gaps via silence detection; keep
   A/V in sync and smooth the jump cuts.
3. **Auto-header** — Sonnet vision agent: early-frame snapshot + transcript +
   optional user description → ≤2-line hook, with the manual header as fallback.
   The emoji spike is resolved in v1 via the PNG-overlay fallback
   (`docs/spikes/emoji-burn-in.md`).

## Wave 2 — early additions

- **User-authored caption presets and finer controls** — custom font/color
  entry, arbitrary position controls, and persistent saved presets remain
  deferred until the bounded built-in choices prove insufficient.

## Deferred — longer-term

- **Filler-word trimming** ("um"/"uh") — transcript-driven cuts on word
  boundaries. Harder than silence-only.
- **Landscape / mixed input + reframe** — active-speaker detection and auto-crop
  to vertical. The single biggest cost in the whole concept; arguably its own
  project.
- **Tier-3 animated captions** — spring/bounce motion, animated resizing boxes.
  Requires adopting a second render engine (compositing / HTML-to-video). This is
  a deliberate engine decision, not a style toggle.
- **Auto-ducking + vocal isolation** — music automatically dips under speech;
  isolate vocals from a music-laden source. Beyond v1's fixed-level mix.
- **Path 2** — 5–10 min input → LLM clip extraction (single-context) on this
  render chassis.
- **Path 1** — 30+ min input → chunked extraction with global re-ranking. The
  full Opus-Clip problem.
