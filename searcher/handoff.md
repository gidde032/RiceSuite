# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; product contract →
`SPEC.md`/`ADR-001.md`/`ADR-002.md`; planned work → GitHub Issues.

Last updated: 2026-09-20.

## Verified main state

- Main is `ca72396`; the focused branch below was created from that commit.
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

## Active delivery — exact reviewed clip window

- Owning Issue: [#32](https://github.com/gidde032/RiceSearcher/issues/32).
- Milestone: v1 — content-sourcing prototype.
- Branch: `fix/exact-review-window` from `ca72396`.
- Draft PR: not opened yet; first coherent commit is locally green.
- Contract: ADR-001 Q4 amendment (2026-09-20), SPEC FR-8/FR-9 and §§6–7.
- Authorized: scoped implementation, tests, documentation, branch/commits/push,
  draft PR, independent review, in-contract repairs, and CI monitoring.
- Withheld: merge, publish, deploy, visibility changes, tag, and release.
- Current state: implementation is integrated. All three Luna-max packets hit their
  usage limit after adding partial regression work, so the parent audited every
  hunk, completed the implementation, and added the missing persisted-transcript
  integration regression. Green evidence: Ruff format/lint; mypy on 35 source
  files; JS syntax; 17 Node behavior tests; 224 Python tests at 94.06% coverage;
  pinned smoke tier 5. The two untracked local profile JSON files are unrelated
  user work and remain untouched and unpublished.

## Next action

Create the first coherent commit and draft PR, then freeze the exact base/head
target for three-reviewer cold review.

## Deferred

#9 is a RiceClipper roadmap edit outside this batch. #15 adds explicit search-query
acquisition and needs a separate feature contract/review. #7 remains the deferred
scheduled watcher. See `ROADMAP.md` and live GitHub Issues for current status.

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release.
