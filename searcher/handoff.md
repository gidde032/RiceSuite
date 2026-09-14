# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; keep under ~200
lines. Shipped history → `CHANGELOG.md` (created when versioned); product
contract → `SPEC.md`/`ADR-001.md`; planned work → GitHub Issues.

Last updated: 2026-09-14.

## Current state

- Bootstrap complete. `ADR-001.md` ACCEPTED; `SPEC.md` RATIFIED (D1–D8). GitHub:
  private `gidde032/RiceSearcher`; milestone #1; Issues #1–#9; templates; CI.
- ✅ **Phase 1 MERGED** (PR #10, `52c386c`): acquire (yt-dlp + watch-folder) →
  content-addressed cache → faster-whisper transcript → SQLite library + CLI.
- ✅ **Phase 2 MERGED** (PR #11): beat profile, prefilter, LLM scorer (Haiku 4.5
  default; key via `credentials.env`/export), extract_and_score, `score`/`slices`
  CLI. Taste-validated live (protocol on #6). Closed deferred D1.
- ✅ **Phase 3 MERGED** (PR #12, `a85309c`): advisory dedup signal (intra time-overlap
  + cross-source transcript embedding via lazy/swappable `sentence-transformers`);
  `dedup` CLI with `--threshold` + human-readable output; default cosine 0.65.
  Never filters/hides/reorders. 3-reviewer cold review; repairs with regressions.
- ✅ **Phase 4 MERGED** (PR #13, `eb2f6e7`): Slate review UI + select gate.
- ✅ **Phase 5 MERGED** (PR #14, `05e2d79`): manifest-last Searcher handoff
  writer, CLI/UI send path, and own `~/ricesearcher-handoff` root.
- Maintainer live-verified the full chain through RiceClipper render and RicePoster
  caption generation.

## Shipped — v1 multi-lens hardening (PR #16)

Round 1 reviewed frozen main `05e2d79` with five independent lenses. The approved
repair batch is integrated locally on `review/v1-round1-repairs` (not pushed or in
a PR):

- repeated source pulls update in place and preserve every slice lifecycle state;
- handed-off slices are terminal in the review API/UI;
- producer-owned yt-dlp temp directories are cleaned after cache custody and on
  failures, without deleting caller-owned directories;
- incomplete/malformed scorer results fail visibly instead of becoming zero scores;
- ffmpeg/execution failures return structured retryable UI errors and successful
  handoff confirmation remains visible;
- FR-7 is reconciled to the maintainer's UI-owned detail/filter workflow;
- Phase/status/data-root docs are current; the smoke command is documented and green;
- search-query acquisition is deliberately post-v1 and tracked by Issue #15.

Local integrated gates: ruff + JS clean; **142 tests, 96.05% coverage** (floor 90%);
smoke tier **5 passed** with `pytest -m smoke --no-cov`.

Round 2's approved practical repair batch is also integrated locally:

- correct, duration-matched H.264/AAC handoff clips for offset source streams;
- merged yt-dlp output selection and normalized publication dates;
- terminal handed-off windows, strict scorer results, and atomic re-scoring;
- failed-pull cache cleanup and contextual lazy transcription failures.

Round 3 caught and repaired merged-output selection, model-load context, and the
first timing repair's duration/performance regressions. Current gates: ruff + JS
clean; **155 tests, 95.59% coverage**; smoke tier **5 passed**.

## Shipped since — review-UI polish

PR #18 (Issue #17) **merged** at `f5cdd8a`: media-management page (`/media`,
full-purge, FR-8a), the PNG logo, review-card alignment. Deferred: #19
(media_path normalization), #20 (JS test harness). Gates at merge: 168 tests,
95.74% coverage, smoke tier 5.

## Active work — saved profiles (ADR-002, SPEC D9)

Ratified 2026-09-14. Contract: `docs/design/profiles-spec.md`. Milestone 2.

- Issue **#22** backend P1–P4, branch `feat/profiles-backend` (off `f5cdd8a`).
  First commit: `docs: ratify ADR-002`. Draft PR opens after the first green
  coherent code commit.
- Issue **#23** UI P5. Second branch `feat/profiles-ui` after #22 merges.

Verified: main gates green at `f5cdd8a` (168 tests, 95.74%, smoke 5).
Assumed: the maintainer's live library is schema v2 with `selected`/`handed_off`
rows; the v3 migration must keep them (fixture DB proves it).

Known gaps found at phase start (not blockers):
- No type-check tool is installed. "type-check" in the spec gate list is not
  enforced today. Do not add one inside #22.
- CI runs `node --check` on `app.js` only. #22 or #23 extends it to every
  `web/static/*.js`.

## Next action

Implementer session on `feat/profiles-backend`: build P1→P4 per #22. Exact
first command: `git checkout feat/profiles-backend && pytest -q`.

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
