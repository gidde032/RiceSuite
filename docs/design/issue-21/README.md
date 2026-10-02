# Issue #21 — progress and Searcher review discovery

Owner: [RiceSuite #21](https://github.com/gidde032/RiceSuite/issues/21).
Status: product and visual direction ratified by Finn on 2026-09-30.
[Feature specification](feature-spec.md) consolidates the approved scope and
contains the bounded implementation plan, approved by Finn on 2026-10-01.
[Selected reference](progress-reference.html) shows Searcher's restructured
review and both new status-bar presentations.

## Decision challenge record

Three rounds, one question each, established these choices:

1. **Real per-clip progress**, mirroring Poster's informative staged tracker.
   Keep meaningful stages and a current-action line; avoid detailed percentages,
   processing logs, or a complex monitoring system. Finn selected actual
   progress over simply restyling existing batch strings.
2. **Current operation and latest result**, with reconnection while that server
   operation remains available. Permanent history and recovery after a server
   restart are outside this increment. Finn selected this over durable run
   storage and interruption recovery.
3. **Wider Searcher workspace**, with two results per row in score order
   (1–2 / 3–4) and one column when space is insufficient. Finn selected this
   over retaining the existing 1,100 px maximum. The selected reference and
   feature specification now state concrete responsive geometry.

Material assumptions exposed: Searcher's handoff currently has only a final
response; Clipper's pull and send also have final batch responses. Genuine
per-item transparency requires bounded progress reporting at those stages.
Clip preparation does not establish completed batch delivery: the manifest-last
commit remains the delivery boundary. A missing reply is not proof of failure.
These constraints govern every visual option.

## Evidence and constraints

- `searcher/ricesearcher/web/static/app.js`: `handoff()` awaits one final
  response; `load()` sorts scores descending. Its local stylesheet caps results
  at 1,100 px and currently renders one card per row.
- `clipper/web/app.js`: per-clip ingest and render states already exist; pull
  and send await final batch responses. Render-all continues past a failed clip.
- `poster/frontend/index.html`: `renderProgress()` and `renderSummary()` pair
  concise run activity with per-item outcomes. Reuse the presentation idea,
  without importing Poster code or contacting posting surfaces.
- [Slate ownership](../slate-ownership.md): shared palette already shipped in
  #6. New page geometry and states remain local; the issue's original statement
  about three palette copies is obsolete.
- [Clipper editor layout](../../../clipper/docs/design/editor-layout-spec.md):
  the current three-part upper row and full-width text review remain constraints
  for these tracker additions.
- Suite [ADR-001](../../adr/ADR-001-ricesuite-consolidation.md) and root
  operating rules preserve human selection, review/render, and posting gates.
  Clipper still auto-pulls and auto-sends under the existing conditions; Poster
  still ingests only on its explicit Pull action.

## Preview round 1

[Comparison](progress-layout-options.html) is a self-contained, interactive
mockup fragment. All content, clips, and operation states are synthetic.
It makes no API calls and does not load any application or live data.

- **A — Batch panel:** compact expanded tracker above the workspace, with one
  stage row per participating clip. Keeps activity together; uses vertical space.
- **B — Card status:** one condensed summary above the workspace, with stage
  outcomes on the relevant cards. Maximizes review space; offscreen cards can
  hide details of a long batch.
- **C — Side rail:** dedicated tracker alongside the workspace on wide screens,
  stacking above it below the rail breakpoint. Gives activity a stable place;
  uses horizontal space otherwise available for review.

The agent recommended A. Finn selected **B** and approved the Searcher
restructure alongside the Search/Clip status-bar updates. A and C remain
unselected exploration evidence.

Finn explicitly limited Clipper to changing the progress bar: its slots,
rendered preview, editor design, and layout remain as implemented. The
comparison's simplified Clipper editor is therefore **not** an implementation
reference. Existing per-clip status locations remain; no footer is added to
Clipper cards. The selected Clip reference depicts only the new bar.

## Verification evidence

The original comparison was checked with synthetic data in a throwaway browser
at 390, 1024, and 1440 px: 90 state/layout combinations, no runtime exceptions
or horizontal overflow. Its screenshots are exploration evidence, not tests of
production behavior. The selected reference has its own geometry/state checks
in `reference-checks.json` and reference screenshot files.

Production checks on 2026-10-01: suite 302 tests / 92.85% coverage, Searcher
324 / 94.36%, Clipper 487 / 91.84%; root page behavior 93 cases, Searcher
24 cases, and Clipper progress behavior 12 cases. Existing coverage floors and
lint/format/type gates pass. Synthetic request tests show real intermediate
progress while handoff responses remain pending.

[`browser-checks.json`](browser-checks.json) records eight production viewport
sizes, including both Clipper content modes. The slot template, original CSS
prefix, and card-relative geometry/styles match base `bfa2d51`, including
empty/busy/stale states and the 9:16 frame. Searcher geometry and wrapping pass.
Finn accepted the bar above the unchanged editor on 2026-10-01, including the
short scroll to Download at some laptop sizes. The existing browser contract
now allows only the bar's measured extra space and verifies Download remains
reachable by scrolling. No editor geometry has been changed to compensate.

Production synthetic screenshots: [Search desktop](implemented-search-1440.png),
[Search mobile](implemented-search-390.png), [Clip desktop](implemented-clip-1440.png),
and [Clip mobile](implemented-clip-390.png).

## Delivery

Implementation follows the approved contract. Production browser evidence is
included here; the owning PR records review, CI, and delivery status.

## Maintainer visual refinements — 2026-10-01

Finn requested two corrections after reviewing the implementation:

- Post's narrow navigation keeps its intrinsic 57 px height, including when
  the active view has little content. Grid rows previously distributed unused
  viewport space to the icon strip. Desktop navigation and all handlers stay
  unchanged.
- Clip's operation bar matches Search's system font, muted grey stage/count/
  detail text, 10 × 12 px padding, and 8 × 16 px row gaps. The operation title
  stays bright; existing editor styling and each pillar's error red remain.

[`check_ui_refinements.py`](../../../scripts/check_ui_refinements.py) verifies
18 production fixture cases at 390, 490, 768, 860, 861, and 1440 px: short and
long Post content, compact nav/header, no horizontal overflow, and computed
bar styles. All requests are intercepted; Poster scripts are stripped before
rendering. The checks reproduced the tall navigation and mismatched bar styles
before their respective fixes. The existing 24 production browser cases still
pass with identical Clipper slot geometry. The earlier reference screenshots
record the initial approval; the selected HTML reference and production Clip
screenshots now reflect the requested styling correction.

Updated evidence: [Post compact navigation](refined-post-490.png),
[Clip completed bar](refined-clip-bar-390.png), and
[`refinement-checks.json`](refinement-checks.json).

Local gates: suite 302 passed / 92.85% coverage; Clipper 487 / 91.84%; Poster
1305 passed, 5 documented skips / 88.84%, including its 2-second smoke gate,
in a disposable tracked-only checkout under `POST_MODE=mock`. The working
checkout's unrelated local caption-preset sensitivity check fails against
imported history; the clean checkout excludes those private presets. No local
presets or live state were changed.
