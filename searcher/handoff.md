# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; product contracts →
`SPEC.md`/`ADR-001.md`/`ADR-002.md`; planned work → GitHub Issues.

Last updated: 2026-11-15.

## Active delivery — automatic boundaries and Review controls

- Owning Issue: [RiceSuite #23](https://github.com/gidde032/RiceSuite/issues/23),
  with no milestone. Delivery: [PR #100](https://github.com/gidde032/RiceSuite/pull/100),
  branch `feat/searcher-boundary-refinement-23`, targeting `main`.
- The maintainer accepted the new clips and final Review controls on 2026-11-15
  after independent review, repairs and visual testing. No acceptance blocker
  remains. Merge and formal ready-for-review transition remain maintainer-only.
- Final behavior: `SPEC.md §14`. Online full-transcript discovery/refinement
  has bounded chunk/heuristic fallback; scores use the exact final interval's
  original intersecting source words. Explicit offline scoring remains available.
- Review saves valid playhead marks/time edits immediately without reloading
  media. The control order is Reset bounds → Preview window → Preview ±10s.
  Reset status is separate. Original bounds survive edits/reloads; scores,
  statuses, human-edit protection and selection/handoff gates stay intact.
- Library schema is v5; Searcher-to-Clipper manifest remains schema 1 with exact
  reviewed bytes. The source of truth for exact PR head, current CI, review
  history and documentation accounting is PR #100, rather than this handoff.

## Verified baseline and boundaries

- Expanded workspace delivery [#96](https://github.com/gidde032/RiceSuite/issues/96)
  is closed and [PR #97](https://github.com/gidde032/RiceSuite/pull/97) is merged.
  Source inspection/manual segments and profile-scoped Review are implemented
  (`SPEC.md §13`).
- Verification uses isolated fixtures/copies. Do not open the live library,
  either live handoff root, Clipper work, or Poster accounts/sessions/credentials.
  No candidate footage was rendered or exported by the wrap-up sweep.
- Separate worktrees may contain unrelated maintainer edits. Preserve them;
  do not copy or reset another worktree's handoff during integration.
- Private experiment/review evidence stays in ignored local material. No
  profiles, transcripts, source footage or credentials belong in GitHub.

## Exact next action

The maintainer marks PR #100 ready and merges it. Its `Closes #23` reference
closes the issue on merge. After merge, select the next Issue and refresh this
current-state handoff; do not record a merge or issue closure before it happens.

## Sanity checks

From the suite root: `git status --short` and inspect the current PR head/checks.
For scoped verification, use the Searcher gates in `CLAUDE.md` with temporary
paths and `POST_MODE=mock`. Do not weaken coverage floors or required checks.

## Reserved from the agent (maintainer-only)

Marking PRs ready, merge, publish, deploy, repository-visibility change,
tag/release, and real account/session access without occasion-specific sign-off.
