# RiceClipper — Roadmap

The `SPEC.md` governs **v1**. This document owns everything after it, so the spec
can stay stable while the roadmap churns. Ordering reflects the principle behind
v1: ship a clean, provable render chassis first, then add features on top of it.

## Immediate next step

The v1 hardening pass is complete and under review in draft PR #1. The next
session begins with a visual render-tuning discovery pass: header typography,
header plate/box and placement, caption font treatment, and text-highlight color
and behavior. First decide the intended look and whether each change belongs in
the default preset or in future UI customization; no visual implementation
scope is ratified yet.

## v1 (current — see SPEC.md)

Decode → transcribe (word-level) → word-highlight captions → manual on-screen
header → blur-pad non-9:16 vertical input → optional added-music (replace / mix
with volume) → export 1080×1920 H.264, through a local FastAPI review UI with a
human-in-the-loop gate. One clip at a time. Single Tier-1 caption preset.

## Wave 1 — fast-follow (the "first improvements" cluster)

1. **RicePoster integration** — outputs drop into the harness's pickup contract
   (naming/folder layout it expects). RiceClipper still performs no posting.
2. **Silence-only trimming** — cut long silent gaps via silence detection; keep
   A/V in sync and smooth the jump cuts.
3. **Auto-header** — Sonnet vision agent: early-frame snapshot + transcript +
   optional user description → ≤2-line hook, with the manual header as fallback.
   The emoji spike is resolved in v1 via the PNG-overlay fallback
   (`docs/spikes/emoji-burn-in.md`).

## Wave 2 — early additions

- **Caption style/position configuration** — expose the Tier-1 knobs (font,
  color, highlight color, position) in the review UI. Cheap because the v1 ASS
  template is already parameterized.

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
