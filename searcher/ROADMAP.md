# RiceSearcher — Roadmap

High-level planning horizons. Every actionable entry links to a GitHub Issue;
Issues are the source of truth for scope and status.

## Milestone: v1 — content-sourcing prototype

The five-phase ladder from `SPEC.md §9`. Each phase ends in a usable increment.

1. ✅ **Phase 1 — Acquire + transcribe skeleton** ([#1](https://github.com/gidde032/RiceSearcher/issues/1), merged PR #10). `pull <url|file>` → cached source + word-level transcript in SQLite. Live-verified end-to-end; gates calibrated (coverage floor 90%).
2. ✅ **Phase 2 — Extract + score** ([#2](https://github.com/gidde032/RiceSearcher/issues/2), merged PR #11). Heuristic prefilter → LLM scoring (Haiku 4.5 default) vs. beat-profile → scored candidate slices with padded + intended in/out. Taste-validated on real on-beat content.
3. **Phase 3 — Dedup signal** ([#3](https://github.com/gidde032/RiceSearcher/issues/3)). Advisory possible-duplicate annotation (never filters).
4. **Phase 4 — Slate review UI + select gate** ([#4](https://github.com/gidde032/RiceSearcher/issues/4)). Visual review/select, tightenable in/out.
5. **Phase 5 — Handoff writer** ([#5](https://github.com/gidde032/RiceSearcher/issues/5)). Mirrored superset manifest batch on select.

## Routed forward (tracked, not in the v1 arc)

- **Taste spike** ([#6](https://github.com/gidde032/RiceSearcher/issues/6)) — LLM segment-scoring prototype on real episodes; gates any move toward auto-select.
- **Scheduled-monitor watcher** ([#7](https://github.com/gidde032/RiceSearcher/issues/7)) — future enhancement; on-demand pull is the v1 base.
- **RiceClipper "Pull from Searcher" consumer** ([#8](https://github.com/gidde032/RiceSearcher/issues/8)) — cross-repo work, its own approval.
- **RiceClipper roadmap edit** ([#9](https://github.com/gidde032/RiceSearcher/issues/9)) — retire Path 1/Path 2 extraction into RiceSearcher.
