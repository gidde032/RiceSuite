# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; product contract →
`SPEC.md`/`ADR-001.md`/`ADR-002.md`; planned work → GitHub Issues.

Last updated: 2026-09-18.

## Verified main state

- Main was `dceb3e1` at session start, with green GitHub CI and no open PRs.
- Phases 1–5 are merged. The v1 acquire/transcribe → score/dedup → review/select
  → handoff flow and practical hardening (PR #16) are delivered.
- PR #18 delivered media management, the PNG logo, and review-card polish.
- Saved profiles are delivered: backend PR #24 (#22), UI PR #26 (#23), and
  hardening PR #27 are merged. Contract: `docs/design/profiles-spec.md`, ADR-002,
  SPEC D9. The library uses schema v3 and profile-scoped slices and handoffs.
- Searcher-to-Clipper pickup (#8) and the taste spike (#6) are closed. The
  maintainer previously live-verified the full chain through Clipper render and
  Poster caption generation; this session did not repeat that live validation.
- Existing CI enforces Ruff, syntax checks for all static JS, Node behavior tests,
  and pytest with a 90% coverage floor. The pinned smoke tier contains five tests.

## Current maintenance batch

The maintainer authorized #19, #20, and #25 with Luna xhigh implementation and
parent-side validation, plus this status refresh. Each Issue has a focused branch.

- #19: make the shared-media reference guard handle equivalent path spellings,
  including existing non-normalized rows, with disposable-file regressions.
- #20: extend the existing Node harness to media deletion/clear confirmation,
  timer, and fetch-error flows; the original Issue's missing-harness description
  predates the tests added in PR #27.
- #25: add the mypy gate, scoped optional-import exceptions, and minimal typing
  repairs. This branch documents the new gate; the Issue stays open until merge.

Parent acceptance passed for the combined batch: **215 Python tests, 94.06%
coverage; 12 Node behavior tests; mypy (35 source files), Ruff, JS syntax, and
the five-test smoke tier**. The shared-file regression was independently red
against main and green with the fix. A temporary removed-cancellation probe
confirmed the clear-all timer regression detects the stale-timer risk.

No live library, cache, handoff, acquisition, scoring API, or posting surfaces
were used for validation. Draft PRs remain subject to maintainer review/merge;
this batch used parent-side validation rather than the formal phase review.

## Deferred

#9 is a RiceClipper roadmap edit outside this batch. #15 adds explicit search-query
acquisition and needs a separate feature contract/review. #7 remains the deferred
scheduled watcher. See `ROADMAP.md` and live GitHub Issues for current status.

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release.
