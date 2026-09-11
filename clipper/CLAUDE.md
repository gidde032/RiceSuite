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
the hardening pass, the bounded visual presets, the bounded batch review/render,
the producer-side handoff writer, the Slate browser-interface redesign, and the
four fixed lyric caption presets. The RicePoster-side pickup + auto-caption is
tracked separately (RicePoster #77). Further changes still require explicit
approval and must remain within the active phase.

**Quality gates are enforced in CI.** `.github/workflows/ci.yml` runs ruff
lint + format and the full test suite with an **85% coverage floor** on every
PR, including stacked PRs, and on every push to `main`; `tests/test_gates.py`
locks the CI trigger and gate numbers (smoke count, coverage floor, ruff gates)
so they cannot silently drift. Before calling a change done, run
`ruff check . && ruff format --check .` and
`pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85`.
Ruff config is in `pyproject.toml`; the two-tier `pre-commit` hooks mirror CI
locally. The `main` branch-protection ruleset is prepared in
`.github/rulesets/main.json` but not yet applied — repository rulesets require
GitHub Pro on a private repo.

The **color-emoji burn-in spike** (`docs/spikes/emoji-burn-in.md`) passed via the
PNG-overlay fallback. The Wave-1 auto-header remains deferred product scope,
but is no longer blocked by that spike.

## Hard rules

1. **No posting, publishing, or content upload — ever.** RiceClipper reads local
   files and writes local files. It performs no social posting. The only outbound
   network call in the whole design is the *deferred* header agent (Wave 1), which
   generates text and posts nothing. Posting and its approval gate belong to
   **RicePoster**, a separate repo, after RiceClipper writes the local handoff.
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
RiceClipper produces clips and writes them to the implemented local pickup
contract. RicePoster pulls those files separately and owns all posting behavior.
RiceClipper still does not post; the posting safety gate stays on the RicePoster
side. See `ROADMAP.md` and `docs/integration/riceposter-handoff.md`.

## Relationship to RiceSearcher (upstream)

Separate project, separate repo. **RiceSearcher** finds source material and
surfaces selected clips; it writes them to its **own** handoff root
`~/ricesearcher-handoff` (`RICESEARCHER_HANDOFF_DIR`). RiceClipper **pulls** from
that dir (`RICECLIPPER_SEARCHER_INBOX`, same path) via `POST /api/pull-from-searcher`
/ the "Pull from RiceSearcher" button, ingesting each clip as a review job. Its
own output to RicePoster (`~/riceclipper-handoff`) is unchanged. So RiceClipper is
the **intermediary**: it reads `~/ricesearcher-handoff` and writes
`~/riceclipper-handoff`; RiceSearcher and RicePoster never share a directory. See
`docs/integration/searcher-pickup.md`. Still no posting or network here.
