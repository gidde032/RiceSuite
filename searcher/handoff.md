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

- ✅ **Phase 4 MERGED** (PR #13, `eb2f6e7`): Slate review UI + select gate
  (`ricesearcher review`). 3-reviewer cold review; repairs with regressions.

**Phase 5 — Handoff writer (Issue #5): review-ready on branch `phase-5-handoff`**,
draft PR pending. Searcher-side only (per maintainer: Clipper side planned, not
built). On select, `hand_off_selected` extracts each selected slice's padded window
(ffmpeg, lazy/injectable) and writes a mirrored **manifest-last** batch to the shared
handoff root (superset schema: source_ref/provenance, source_window +
**clip-relative** intended in/out, transcript, score, rationale, rights_risk,
beat_profile_version; stable batch_id; path containment; no orphan on failure), then
marks the slices `handed_off`. Surfaced via `ricesearcher handoff` + a "Send selected"
button in the review UI (`POST /api/handoff`). **RiceClipper "Pull from Searcher"
consumer is PLANNED in `docs/integration/riceclipper-pickup-plan.md` (Issue #8) — to
build after Phase 5 merges, as the targeted RiceClipper edit that wraps v1.**

**Maintainer to do:** (1) live-test `ricesearcher handoff` on a real selected library
(needs ffmpeg — already a system dep); (2) mark the Phase-5 PR ready + merge; then the
**routed-forward Clipper pickup (#8)** + **Clipper roadmap edit (#9)** wrap v1.
**Merge stays maintainer-only** (harness-enforced).

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
