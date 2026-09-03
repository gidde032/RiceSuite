# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; keep under ~200
lines. Shipped history → `CHANGELOG.md` (created when versioned); product
contract → `SPEC.md`/`ADR-001.md`; planned work → GitHub Issues.

Last updated: 2026-09-02.

## Current state

- Bootstrap complete. `ADR-001.md` ACCEPTED; `SPEC.md` RATIFIED (D1–D8). GitHub:
  private `gidde032/RiceSearcher`; milestone #1; Issues #1–#9; templates; CI.
- ✅ **Phase 1 MERGED** (PR #10, `52c386c`): acquire (yt-dlp + watch-folder) →
  content-addressed cache → faster-whisper transcript → SQLite library + CLI.
- ✅ **Phase 2 MERGED** (PR #11, `c3a8e2b`): beat profile, heuristic prefilter, LLM
  scorer (Haiku 4.5 default, env/flag overridable; `anthropic` runtime dep; key via
  `credentials.env`/export), extract_and_score pipeline, `score`/`slices` CLI. Closed
  deferred D1 (real schema migration). 3-reviewer cold review → repairs with
  regressions (deferred C9/C12–C16 noted on #2). **Taste-validated live** on real
  on-beat Person A/Person B content: sensible score spread + rationales, top slices match
  good moments; Sonnet 4.6 marginally better than Haiku but Haiku kept. Taste-spike
  protocol recorded on #6.
- Gates on main: ruff clean; **83 tests, ~96.9% cov** (floor 90%); pinned smoke tier.

## Next action

**Phase 3 — Dedup signal (Issue #3)** on branch `phase-3-dedup`: **advisory-only**
"possible duplicate" annotation — intra-source (source-id + time overlap) +
cross-source transcript-embedding similarity (local model). **Never filters,
discards, blocks, or deprioritizes** a slice (hard rule — populates the existing
`dup_of`/`dup_score`/`dup_kind` columns only). Draft PR after first green commit;
independent review before ready. **Merge stays maintainer-only** (harness-enforced).

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
