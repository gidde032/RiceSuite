# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; keep under ~200
lines. Shipped history → `CHANGELOG.md` (created when versioned); product
contract → `SPEC.md`/`ADR-001.md`; planned work → GitHub Issues.

Last updated: 2026-09-02.

## Current state

- Bootstrap complete. `ADR-001.md` ACCEPTED; `SPEC.md` RATIFIED (D1–D8). GitHub:
  private `gidde032/RiceSearcher`; milestone #1; Issues #1–#9; templates; CI.
- ✅ **Phase 1 MERGED** to `main` (PR #10, merge commit `52c386c`). Acquire
  (yt-dlp + watch-folder) → content-addressed cache → faster-whisper transcript →
  SQLite library, with CLI (pull/list/show). Independently reviewed (3 cold
  reviewers), repairs landed with fail-before-fix regressions, live-verified
  end-to-end. faster-whisper/ctranslate2 4.8.2 confirmed working on Python 3.14.
- Gates on main: ruff clean; **38 tests, 97% cov** (floor 90%); pinned smoke tier.

## Next action

**Phase 2 — Extract + score (Issue #2)** on branch `phase-2-extract-score`:
heuristic prefilter → candidate windows → LLM scoring vs. the versioned
beat-profile (NL brief + few-shot) → scored candidate slices persisted with the
SPEC §6 schema (padded window + intended in/out). Also carries deferred **D1**
(real schema-version migration path). Open a draft PR after the first green commit;
independent review before ready. **Merge stays maintainer-only** (harness-enforced).

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
