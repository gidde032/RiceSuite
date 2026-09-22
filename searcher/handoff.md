# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; product contract →
`SPEC.md`/`ADR-001.md`/`ADR-002.md`; planned work → GitHub Issues.

Last updated: 2026-09-21.

## Verified main state

- Main is `576c73c` (the PR #33 merge). Phases 1–5 are merged: the v1
  acquire/transcribe → score/dedup → review/select → handoff flow plus practical
  hardening.
- Saved profiles are delivered and hardened: backend #24 (#22), UI #26 (#23),
  hardening #27. Contract: `docs/design/profiles-spec.md`, ADR-002, SPEC D9. The
  library uses schema v3 with profile-scoped slices and handoffs.
- Shared-media path preservation on source delete: #29 (closes #19) merged.
- Exact reviewed clip window: #33 (closes #32) merged. The saved source-bounded
  interval controls preview, extraction, schema-1 manifest bounds, and transcript
  intersection.
- Searcher-to-Clipper pickup (#8) and the taste spike (#6) are closed. The
  maintainer previously live-verified the full chain through Clipper render and
  Poster caption generation; recent sessions did not repeat that live validation.
- CI enforces Ruff, JS syntax checks, Node behavior tests, and pytest with a 90%
  coverage floor. The pinned smoke tier contains five tests.

## Active delivery — post-merge review repairs (PR #27/#29/#33)

- A post-merge independent multi-agent review of #27/#29/#33 (five isolated cold
  reviewers + OCR pre-gate) triaged six findings; the report is
  `internal/review-pr27-29-33-triage.md`.
- Branch: `fix/review-27-29-33-repairs` from `576c73c`.
- PR: [#34](https://github.com/gidde032/RiceSearcher/pull/34), open for maintainer review.
- Repairs applied (all with fail-before-fix regressions):
  - **A** — handoff no longer holds the SQLite write lock across the ffmpeg
    encode; a short critical section re-reads and marks handed_off only if the
    snapshot is still exactly selected. Concurrent review writes no longer 500.
  - **B1** — the manifest records the *measured* exported clip duration/window,
    never overstating the delivered bytes.
  - **B2** — handoff clamps `target_out` to the verified source duration instead
    of 409-ing the whole batch; a window at/after source end still fails closed.
  - **C** — unusable ffmpeg output raises `ClipExtractError`, mapped to a graceful
    503 (retryable) instead of a raw 500.
  - **D** — removed the dead `HandoffEntry.pad_in/pad_out` fields.
  - **E** — re-anchored `PROFILE_ID_PATTERN` (`\A…\Z`, safe-by-default).
- A three-reviewer Luna/max validation of PR #34 found four material follow-up
  defects; all are repaired with observed fail-before-fix regressions:
  - concurrent edits to an in-flight selected window now invalidate the snapshot
    instead of publishing stale bytes and terminally marking the edited row;
  - concurrently prepared batches stay consumer-invisible until final arbitration,
    so a losing handoff never exposes its `manifest.json`;
  - non-finite stored windows fail closed before source-duration clamping; and
  - manifest transcript metadata stops at the measured exported end. The writer
    also rejects non-finite or non-positive extractor duration results.
- Docs reconciled to the new behavior: SPEC §7 + D8, ADR-001 Q4/trade-offs,
  README review workflow, and the RiceClipper pickup plan.
- Green evidence: Ruff format/lint; mypy on 35 source files; JS syntax; 17 Node
  behavior tests; 252 Python tests at 93.72% coverage; pinned smoke tier 5.
- Authorized: scoped repairs, tests, documentation, branch/commits/push, and PR
  creation. Withheld: merge, publish, deploy, visibility changes, tag, release.

## Next action

Verify PR #34's repository-owned `gates` check after the validation-repair push,
then maintainer review. Merge remains explicitly withheld from the agent.

## Deferred

- Housekeeping note from the review: `Library.upsert_slices` may be test-only
  after #27 — verify on a future `store.py` touch (not actioned).
- #9 is a RiceClipper roadmap edit outside this batch. #15 adds explicit
  search-query acquisition and needs a separate feature contract/review. #7 is the
  deferred scheduled watcher. See `ROADMAP.md` and live GitHub Issues.

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release.

## Untracked local files (not this session's work)

`ricesearcher/beat/profiles/lighter-rap.json` and `rap-edits.json` are unrelated
user profile experiments; they remain untracked and are excluded from the PR.
