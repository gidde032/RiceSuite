# CLAUDE.md — operating context for RiceClipper

This file orients an AI agent session working in this repository. Read it before
acting.

## What this project is

RiceClipper is a standalone tool that turns short vertical videos into post-ready
clips with word-synced burned-in captions and an on-screen header. The **source
of truth for the design is [`SPEC.md`](./SPEC.md).** Do not re-derive scope from
memory or chat — read the spec.

## Current status

**Full v1 → Wave-1 clip pipeline implemented and confirmed functional end to
end** — upload → RiceClipper batch review/render → filesystem handoff →
RicePoster "Pull from Clipper" → post. Merged to `main`: the v1 vertical slice,
the hardening pass, and the bounded visual presets. Implemented and verified,
pending PR merge: bounded batch review/render (PR #4), the producer-side handoff
writer (PR #5), and the RicePoster-side pickup + auto-caption (RicePoster #77).
Further changes still require explicit approval and must remain within the active
phase.

The **color-emoji burn-in spike** (`docs/spikes/emoji-burn-in.md`) passed via the
PNG-overlay fallback. The Wave-1 auto-header remains deferred product scope,
but is no longer blocked by that spike.

## Hard rules

1. **No posting, publishing, or content upload — ever.** RiceClipper reads local
   files and writes local files. It performs no social posting. The only outbound
   network call in the whole design is the *deferred* header agent (Wave 1), which
   generates text and posts nothing. Posting and its approval gate belong to
   **RicePoster**, a separate repo, at the future integration point.
2. **No implementation code without explicit approval.** When a build task comes
   up, first present a triage/plan (what will change, where, why) and get a clear
   yes. Do not start writing modules because the design is settled — a ratified
   design is not authorization to build.
3. **Respect phase boundaries.** Work proceeds in explicit phases; the human
   approves each transition. Do not roll from v1 into Wave-1 features without an
   approved phase change.
4. **Documentation is a first-class deliverable.** Keep `SPEC.md`, `ROADMAP.md`,
   and `CHANGELOG.md` current alongside code, not after it.

## Source-of-truth map

- **`SPEC.md`** — v1 design, decision log (D1–D13), boundary/safety, tech stack.
- **`ROADMAP.md`** — post-v1 sequencing (Wave 1 / Wave 2 / deferred).
- **`CHANGELOG.md`** — what actually shipped.
- **`docs/spikes/`** — de-risking investigations and their pass/fail results.
- **`docs/adr/`** — reserved for future architecture decision records if the
  design starts evolving through many recorded revisions.

## Stack (from SPEC.md §10)

Python + FastAPI (local server) · vanilla HTML/JS review UI · faster-whisper
(word-level transcription) · ffmpeg + libass (ASS subtitle burn-in, blur-pad,
audio mix) · Anthropic Sonnet for the deferred auto-header · local-first
throughout · output 1080×1920 H.264/AAC mp4.

## Relationship to RicePoster

Separate project, separate repo. RicePoster handles the posting automation;
RiceClipper produces the clips it will eventually post. Integration is Wave-1 work
(`ROADMAP.md`). When integrating, RiceClipper still does not post — it only writes
output files into whatever pickup contract RicePoster expects; the posting safety
gate stays on the RicePoster side.
