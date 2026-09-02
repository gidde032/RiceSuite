# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; keep under ~200
lines. Shipped history → `CHANGELOG.md` (created when versioned); product
contract → `SPEC.md`/`ADR-001.md`; planned work → GitHub Issues.

Last updated: 2026-09-02.

## Current state

- Bootstrap complete. `ADR-001.md` ACCEPTED; `SPEC.md` RATIFIED (D1–D8). GitHub:
  private `gidde032/RiceSearcher`; milestone #1; Issues #1–#9; templates; CI.
- **Phase 1 in flight** on branch `phase-1-acquire-transcribe`, **draft PR #10**
  (linked to Issue #1). Walking skeleton committed at `6369b47`:
  config/models/library(SQLite+cache)/acquire(watchfolder+ytdlp)/transcribe(whisper)/
  pipeline/cli. Heavy deps lazily imported behind interfaces.
- Gates green locally: ruff format+lint clean; **29 tests pass, 98.22% cov**
  (floor 90%); 5-test smoke inventory pinned. OQ-1 coverage resolved for Phase 1.

## Next action

Finish Phase 1 on PR #10: (1) exercise the **real yt-dlp + faster-whisper adapters**
against a live pull off-CI and record evidence (note: faster-whisper on Python 3.14
may need a version check — CI runs 3.12); (2) run independent review (3 reviewers
incl. standing skeptic); (3) repair findings with fail-before-fix regressions; then
mark PR ready. **Merge is maintainer-only.**

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
