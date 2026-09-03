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

### PR #14 repair batch IN PROGRESS (3-reviewer cold review done, approved 2026-09-03)
Safety confirmed clean. Fixes (commit each, small pieces):
- [ ] **H1** partial-mark: add `Library.bulk_update_status(ids, status)` (one txn); use in `hand_off_selected` instead of the per-slice loop.
- [ ] **L1/L2/L3** (writer.py): reject inverted/degenerate clip window in `_manifest_clip`; guard `shutil.rmtree` cleanup so the original error propagates; `mkdir` collision → `HandoffError`.
- [ ] **H2** concurrent handoff: in-process lock around the `/api/handoff` op; disable the UI "Send selected" button while in-flight.
- [ ] **D1** reconcile `SPEC.md §7` manifest to `source_window` + `clip`; document the residual two-phase (FS-write-then-DB-mark) gap in SPEC §7 + `docs/integration/riceclipper-pickup-plan.md` (consumer should be content-idempotent, not just batch_id).
Each fix gets a fail-before-fix regression. Then post the public-safe review summary on PR #14 and update this handoff to review-ready.

**Maintainer to do:** (1) live-test `ricesearcher handoff` on a real selected library
(needs ffmpeg — already a system dep); (2) mark the Phase-5 PR ready + merge; then the
**routed-forward Clipper pickup (#8)** + **Clipper roadmap edit (#9)** wrap v1.
**Merge stays maintainer-only** (harness-enforced).

## Reserved from the agent (maintainer-only)

Merge, publish, deploy, repository-visibility change, tag/release. Everything else
in the ratified setup + Phase 1 is authorized.
