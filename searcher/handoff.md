# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; keep under ~200
lines. Shipped history → `CHANGELOG.md` (created when versioned); product
contract → `SPEC.md`/`ADR-001.md`; planned work → GitHub Issues.

Last updated: 2026-09-02.

## Current state

- Repository: `/Users/finngidden/Projects/RiceSearcher`, branch `main`.
- Bootstrap complete through spec ratification. `ADR-001.md` ACCEPTED; `SPEC.md`
  RATIFIED (decisions D1–D8). Both reconciled against live sibling code.
- GitHub setup DONE: private `gidde032/RiceSearcher` pushed; labels, milestone
  `v1 — content-sourcing prototype` (#1), Issues #1–#9 (phases #1–#5,
  routed-forward #6–#9), Issue/PR templates all live.
- Implementation authority: **Phase 1 authorized** by the maintainer (2026-09-02,
  "looks good, go ahead"). Never post/publish/upload; local-first.

## Next action

Start **Phase 1 — Acquire + transcribe skeleton** (Issue #1) on a focused branch:
stand up the Python project + gate config + CI + first smoke test (walking
skeleton, measure OQ-1 numbers), then `pull <url|file>` → cached source +
word-level transcript in SQLite. Open a draft PR after the first coherent green
commit; link it to #1.

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
