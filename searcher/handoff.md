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
- ✅ **Phase 2 MERGED** (PR #11): beat profile, prefilter, LLM scorer (Haiku 4.5
  default; key via `credentials.env`/export), extract_and_score, `score`/`slices`
  CLI. Taste-validated live (protocol on #6). Closed deferred D1.
- ✅ **Phase 3 MERGED** (PR #12, `a85309c`): advisory dedup signal (intra time-overlap
  + cross-source transcript embedding via lazy/swappable `sentence-transformers`);
  `dedup` CLI with `--threshold` + human-readable output; default cosine 0.65.
  Never filters/hides/reorders. 3-reviewer cold review; repairs with regressions.
- Gates on main: ruff clean; **101 tests, ~96.7% cov** (floor 90%); pinned smoke tier.

## Next action

**Phase 4 — Slate review UI + select gate (Issue #4)** on branch `phase-4-review-ui`:
a local FastAPI + vanilla-JS web UI (the human select-and-approve gate, ADR Q5)
matching the **Slate** design system (dark carbon/grey/rice-grey palette, symbol-only,
WCAG AA, reduced-motion, no blue — tokens from `../RiceClipper/docs/design/slate-ui-spec.md`).
Browse scored candidate slices (source title, score, rationale, dup annotation,
transcript, rights), preview the moment's video window, tighten intended in/out, and
Select (→ status `selected`) / Reject (→ `rejected`). No auto-select; never posts.
Draft PR after first green commit; independent review before ready. **Merge stays
maintainer-only** (harness-enforced).

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
