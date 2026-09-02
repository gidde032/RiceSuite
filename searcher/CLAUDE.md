# RiceSearcher — Operating Rules

Content-sourcing pillar of the Rice harness. Discovery (on-demand pull) +
extraction (transcript-driven *time*/trim) → scored candidate slices in a
moment-deduplicated local library → padded-window handoff to RiceClipper.
Siblings: `../RiceClipper` (render), `../RicePoster` (posting).

## Hard rules (non-negotiable)

1. **Never post, publish, or upload content, ever.** No code path contacts any
   posting/publishing/upload surface. Outbound network is limited to source
   acquisition (yt-dlp fetch) and the scoring LLM API — neither touches an
   account, platform, or posting endpoint.
2. **Local-first.** SQLite + local disk only; no cloud storage. Private repo.
3. **Dedup is advisory only.** A "possible duplicate" annotation never filters,
   discards, blocks, or deprioritizes a slice. The maintainer reposts similar and
   re-edited content deliberately.
4. **Human select-and-approve gate stays.** L4 automates *up to* the select gate;
   it is never deleted, only (eventually) made one-click. No auto-select in v1.
5. **Track `rights_risk` on every slice.** Source material is copyrighted; content
   stays off any public surface.

## Source-of-truth order

1. Code and tests for implemented behavior.
2. `SPEC.md` — v1 product contract and decisions D1–D8.
3. `ADR-001.md` — the three-pillar boundary (Q1–Q5).
4. `ROADMAP.md` — future sequencing (links every actionable entry to an Issue).
5. GitHub Issues (backlog) / milestones (committed scope) / PRs (delivery record).
6. `handoff.md` — immediate current state only.

Actionable planned work lives in **GitHub Issues**, not in `handoff.md`,
`pending-lessons.md`, or a local `TASKS.md`. `TASKS.md` (if present) is gitignored
and holds only the current session's execution steps.

## Cross-repo facts (verified 2026-09-02 — see `SPEC.md §7`)

- RiceClipper **blur-pads** non-9:16 to 1080×1920 and **never crops**; blur-pad is
  the accepted v1 "crop" story. Its input is vertical-mostly; reframe is deferred.
- RiceClipper has **no pickup/import side** — it ingests via its upload UI and only
  *writes* handoffs. Our Searcher→Clipper consumer is routed-forward cross-repo work.
- Handoff mechanism to mirror: filesystem pickup, one dir per batch, `manifest.json`
  written **last** = atomicity, FIFO by `created_at`, dedupe by stable `batch_id`,
  producer-only-writes. Reference `../RiceClipper/docs/integration/riceposter-handoff.md`
  and the lessons in `../RiceClipper/internal/riceposter-integration-review.md`.
- Reuse RiceClipper's faster-whisper pattern (`../RiceClipper/transcribe/`).
- Review UI must match the **Slate** design system:
  `../RiceClipper/docs/design/slate-ui-spec.md`.

## Stack & gates

Python + FastAPI, faster-whisper, yt-dlp, local embedding model (dedup),
Anthropic API (scoring). Gates (numbers calibrated at the Phase-1 skeleton, then
ratcheted): `ruff format --check`, `ruff check`, type-check, `pytest` + coverage
floor, a fast smoke tier with a checked count. Weakening a gate is never an
autonomous decision.

## Workflow

Issue-backed phases (`SPEC.md §9`); focused branch per Issue; draft PR after the
first coherent green commit; independent review (3 reviewers incl. a standing
skeptic) before ready. Every accepted finding is repaired with a fail-before-fix
regression or deferred to a linked Issue before a phase closes.
