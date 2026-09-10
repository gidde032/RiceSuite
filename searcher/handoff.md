# RiceSearcher — Handoff

**Read this before editing.** Current-state continuity only; keep under ~200
lines. Shipped history → `CHANGELOG.md` (created when versioned); product
contract → `SPEC.md`/`ADR-001.md`; planned work → GitHub Issues.

Last updated: 2026-09-10.

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

## Active work — v1 multi-lens hardening

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

## Active work — review-UI polish (PR #18, Issue #17)

Branch `feat/media-mgmt-logo-ui-polish` (off merged main `e9ee092`): a
media-management page (`/media`) that lists stored sources and **full-purges**
one source or the whole cache (local-only; SPEC FR-8a), a new magnifier-over-rice
logo, and review-card alignment fixes. 3-reviewer cold review complete; accepted
findings repaired with fail-before-fix regressions (F2/media_path-normalization
and a JS test harness deferred to Issues). Gates: ruff clean; **167 tests, 95.7%
coverage**. PR marked ready for maintainer review — **not merged** (merge is the
maintainer's).

Overnight, 3 Person B Bianco YouTube clips were pulled + scored (Haiku 4.5) + dedup'd
into the library for the maintainer to review/select in the morning; no auto-select.

## Next action

Maintainer: review PR #18 and select from the newly loaded candidate slices in
`ricesearcher review`.

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
