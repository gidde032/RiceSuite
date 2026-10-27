# Searcher Split workspace — ratified reference contract

Ratified by Finn, 2026-10-22, including Q10B, Q11A, and the Q12 kind filter.
Behavior authority: `searcher/SPEC.md §13`.
Delivery: [RiceSuite #96](https://github.com/gidde032/RiceSuite/issues/96).

## Selected artifact and authority

Use **Split workspace** in `mockups/searcher-layouts.html`. Source detail and
Inspector drawer are comparison evidence only. This document amends that
artifact where the final choices or existing production styles differ, following
the suite's existing reference-plus-amendment pattern.

Frozen comparison SHA-256:
`e0497b313e66aa19dee815191d6037ab22e1f6eae2afc47c571041759b1eefe3`.

- The selected artifact owns the persistent library / full-source inspector
  composition, inline Add sources, compact expandable batch strip, timestamp
  segment form, and collapsed advanced settings.
- Canonical `ricesuite/shell/slate.css`, Searcher's existing stylesheet and
  controls, and the existing spacing-unit pattern own appearance. Do not copy
  the prototype's palette, independent spacing values, control sizes, initial
  Sources page, or 1024 px preview-only width cap into production.
- Review remains the landing page and initially shows Candidates, with kind All.
- At 900 px and above, use the available width with 16 px page insets, a source
  library `clamp(240px, 30vw, 360px)` wide, and the remaining width for the
  inspector, separated by 16 px. Derive these insets/gaps from an 8 px
  `--searcher-space` unit. Below 900 px use 8 px page insets, an initial source
  list, and focused source detail with Back to sources after opening a source.
- Source video uses native controls and `object-fit: contain`. Its viewing area
  uses `aspect-ratio: 16 / 9` with a maximum height of `min(56vh, 480px)`;
  preserve the full source frame with letterboxing when needed. The player
  must not expand past that height and push its timestamp form down on laptops.
- Keep existing Searcher review cards: two equal columns from 900 px, one below;
  within a card, stack below 500 px content width and use 34/66 above it.
- Use the existing 15 px body scale, operational Arial convention, canonical
  color roles, native form controls, visible rice-grey keyboard focus, existing
  semantic error/destructive treatments, and at least 36 px fine / 44 px
  coarse-pointer control targets. Retain full framing and current card controls.

## Review and handoff amendment

Add a **Kind** select with All, Manual, and Scored. Combine it with existing
status and source filtering. All groups manual/unscored results first in
creation order, followed by scored results ordered by score and stable
tie-breaks. Each filtered group retains its within-group order.

Saving a manual interval produces a reviewed, unselected manual segment; a
View in Review action reveals it. No numeric model score or stale-model badge
is shown for an unscored manual segment. Existing scored metadata, including
heuristic, offline, stale, rights, rationale, and duplicate annotations, remains.

Send selected remains profile-wide and independent of browsing filters. Show
the total selected count and name the count hidden by current filters. Handoff
positions follow manual creation order first, then scored descending order.
The selected set never changes solely because filters or pages change.

## Required reference and test states

- Review landing, Candidates and All status views, each Kind choice, and combined
  source/kind/status filters; no-match and empty-profile states.
- Shared source library, one/multiple batch selections, inline URL/path/file
  intake, Add only / Add & score, advanced overrides, and missing credentials.
- Full source and transcript, playhead timestamp marks, valid and invalid
  intervals, saved unselected manual segment, and View in Review.
- Batch active, source failure, scoring-only retry, stop-after-current, a changed
  visible profile with the original run profile retained, response/read loss,
  and an expired/restarted run without automatic replay.
- Missing and unsupported media, long titles/transcripts, selected and terminal
  handoff cards, advisory duplicate flags, and destructive confirmations.
- Wide split, 899/900 px boundary, narrow library/detail/back, and fine/coarse
  pointer focus and targets. Verify 320, 390, 768, 899, 900, 1024, 1366, 1440,
  and 1920 px widths at the heights in the implementation plan.

Computed styles and geometry are mandatory acceptance evidence; screenshots of
the production fixture pages supplement them. The old comparison's sample
processing transitions are illustrative and are not the production behavior.
