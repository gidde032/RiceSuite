# RiceSearcher — Roadmap

High-level planning horizons. Every actionable entry links to a GitHub Issue;
Issues are the source of truth for scope and status.

## Milestone: v1 — content-sourcing prototype

The five-phase ladder from `SPEC.md §9`. Each phase ends in a usable increment.

1. ✅ **Phase 1 — Acquire + transcribe skeleton** ([#1](https://github.com/gidde032/RiceSearcher/issues/1), merged PR #10). `pull <url|file>` → cached source + word-level transcript in SQLite. Live-verified end-to-end; gates calibrated (coverage floor 90%).
2. ✅ **Phase 2 — Extract + score** ([#2](https://github.com/gidde032/RiceSearcher/issues/2), merged PR #11). Original delivery: heuristic prefilter → LLM scoring (then-default Haiku 4.5) vs. beat-profile → scored candidate slices with padded + intended in/out. Taste-validated on real on-beat content. Current online discovery/refinement is amended by `SPEC.md §14`.
3. ✅ **Phase 3 — Dedup signal** ([#3](https://github.com/gidde032/RiceSearcher/issues/3), merged PR #12). Advisory possible-duplicate annotation (intra-source overlap + cross-source embedding, threshold 0.65); never filters. `dedup` CLI with `--threshold`.
4. ✅ **Phase 4 — Slate review UI + select gate** ([#4](https://github.com/gidde032/RiceSearcher/issues/4), merged PR #13). `ricesearcher review` — browse, preview, tighten in/out, Select/Reject.
5. ✅ **Phase 5 — Handoff writer** ([#5](https://github.com/gidde032/RiceSearcher/issues/5), merged PR #14). Mirrored superset manifest batch on select (`handoff` CLI + UI button). Clipper-side pickup is tracked separately in [#8](https://github.com/gidde032/RiceSearcher/issues/8).

## Milestone: Saved profiles (ADR-002, SPEC D9)

- ✅ **Profiles backend P1–P4** ([#22](https://github.com/gidde032/RiceSearcher/issues/22), merged PR #24) — loader, schema v3 migration, partitioned pipeline/CLI, `profile_id` in the manifest.
- ✅ **Profiles UI P5** ([#23](https://github.com/gidde032/RiceSearcher/issues/23), merged PR #26; hardening PR #27) — profile select, `/profiles` page, stale badge, scoped handoff.
- Routed forward inside ADR-002: per-profile prefilter weights, profile edit in the UI, cross-profile advisory dedup. Issues open when the need appears.

## Current delivery

- **Automatic boundaries and Review controls** ([RiceSuite #23](https://github.com/gidde032/RiceSuite/issues/23))
  — full-transcript discovery/refinement and the Review playback, mark and reset
  controls in `SPEC.md §14`. Delivery, acceptance evidence and merge status are
  tracked in [PR #100](https://github.com/gidde032/RiceSuite/pull/100).

## Legacy maintenance references

These original-pillar references record earlier follow-up scopes. Their linked
Issues own the final disposition; they are not a separate active suite backlog.
The old #19/#20 URLs no longer resolve, so their original IDs are preserved as
historical references alongside current code/test pointers.

- **Shared-media path matching** (historical RiceSearcher #19) — implemented shared-file protection for equivalent persisted paths; current coverage is in `tests/test_store.py`.
- **Media UI behavior coverage** (historical RiceSearcher #20) — implemented Node coverage for destructive confirmation and error flows in `tests/js/media_ui.test.js`.
- **Offline/LLM score ordering** ([#5](https://github.com/gidde032/RiceSearcher/issues/5)) — mixed-profile ordering policy for heuristic and LLM scores.

## Routed forward (tracked, not in the v1 arc)

- **Scheduled-monitor watcher** ([#7](https://github.com/gidde032/RiceSearcher/issues/7)) — future enhancement; on-demand pull is the v1 base.
- **Search-query acquisition** ([#15](https://github.com/gidde032/RiceSearcher/issues/15)) — first post-v1 feature; v1 supports YouTube URLs/channels and local files.
- **RiceClipper roadmap edit** ([#9](https://github.com/gidde032/RiceSearcher/issues/9)) — retire Path 1/Path 2 extraction into RiceSearcher.

## Completed follow-up work

- ✅ **Expanded Searcher workspace** ([RiceSuite #96](https://github.com/gidde032/RiceSuite/issues/96), merged [PR #97](https://github.com/gidde032/RiceSuite/pull/97)) — Sources intake/inspection, manual segments, profile-scoped Review and serial processing batches. Contract: `SPEC.md §13`.
- ✅ **Offline scoring mode** ([#2](https://github.com/gidde032/RiceSearcher/issues/2)) — `score --offline` runs the flow with the heuristic scorer and records that results were not LLM-scored.
- ✅ **Type-check gate** ([#25](https://github.com/gidde032/RiceSearcher/issues/25)) — `mypy` is configured for Python 3.12 and enforced locally and in CI.
- ✅ **Taste spike** ([#6](https://github.com/gidde032/RiceSearcher/issues/6), closed 2026-09-11) — real-content scoring validation; the human select gate remains.
- ✅ **RiceClipper "Pull from Searcher" consumer** ([#8](https://github.com/gidde032/RiceSearcher/issues/8), closed 2026-09-11) — Searcher-to-Clipper pickup delivered.
