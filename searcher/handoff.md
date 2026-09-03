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

**Phase 1 / PR #10 is review-ready pending maintainer merge.** Done: independent
3-reviewer cold review → approved repair batch (C1–C3, S1–S5) landed with
fail-before-fix regressions; deferred D1→#2, D2→#5. **Live pull confirmed** off-CI:
real yt-dlp (video+audio merge) + faster-whisper transcribed "Me at the zoo" (35
words) end-to-end; `list`/`show` work. faster-whisper + ctranslate2 4.8.2 install &
import fine on Python 3.14 (earlier concern cleared). Two live-surfaced adapter bugs
fixed: yt-dlp needed `bestvideo*+bestaudio` merge (was pulling video-only), and the
transcriber now raises a clear error on audio-less input.

Remaining: maintainer marks PR #10 ready + merges (maintainer-only), then Phase 2
(Extract + score, Issue #2). **Merge is maintainer-only.**

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
