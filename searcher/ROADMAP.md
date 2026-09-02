# RiceSearcher — Roadmap

High-level planning horizons. Every actionable entry links to a GitHub Issue;
Issues are the source of truth for scope and status.

## Milestone: v1 — content-sourcing prototype

The five-phase ladder from `SPEC.md §9`. Each phase ends in a usable increment.

1. **Phase 1 — Acquire + transcribe skeleton.** `pull <url|file>` → cached source +
   word-level transcript in SQLite. (Walking skeleton; calibrate gate numbers here.)
2. **Phase 2 — Extract + score.** Heuristic prefilter → LLM scoring vs. beat-profile
   → scored candidate slices with padded + intended in/out.
3. **Phase 3 — Dedup signal.** Advisory possible-duplicate annotation (never filters).
4. **Phase 4 — Slate review UI + select gate.** Visual review/select, tightenable in/out.
5. **Phase 5 — Handoff writer.** Mirrored superset manifest batch on select.

_(Issue links added once the milestone's Issues are created.)_

## Routed forward (tracked, not in the v1 arc)

- **Taste spike** — LLM segment-scoring prototype on real episodes; gates any move
  toward auto-select.
- **Scheduled-monitor watcher** — future enhancement; on-demand pull is the v1 base.
- **RiceClipper "Pull from Searcher" consumer** — cross-repo work, its own approval.
- **RiceClipper roadmap edit** — retire Path 1/Path 2 extraction into RiceSearcher.
