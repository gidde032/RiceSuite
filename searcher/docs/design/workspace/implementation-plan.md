# Expanded Searcher workspace — implementation plan

Status: **RATIFIED through the final Q10–Q12 answers, 2026-10-22**.
Owner: [RiceSuite #96](https://github.com/gidde032/RiceSuite/issues/96).
Behavior: `searcher/SPEC.md §13`. Selected layout: Split workspace in
`mockups/searcher-layouts.html`. This plan is not the implementation-agent packet.

## Reference and style authority

1. Suite/pillar operating rules, ADRs, and SPEC own behavior and boundaries.
2. `ricesuite/shell/slate.css` owns shared Slate tokens; Searcher's existing
   `ricesearcher/web/static/styles.css` owns its controls and page treatments.
3. The selected Split workspace governs source-library/inspector placement and
   interaction intent. Its independent CSS, 1024 px preview cap, and demo-state
   transitions are not copied into production.

Proposed layout constants follow Clipper's existing spacing pattern: a Searcher
spacing unit of 8 px, with page insets 2× on wide layouts and 1× on narrow layouts,
and derived section/control gaps. Reuse existing control classes and style
tokens; keep existing component-specific sizing when no change is required.
No restyling of Clip, Post, or the suite shell is included.

Use the available browser width, with a bounded source column and a flexible
inspector. Reuse Searcher's 900 px wide/narrow threshold and retain its review
card rules (two columns at 900 px, 500 px card-content threshold, 34/66 split).
Native video shows the full source with `object-fit: contain`. Bound its height
for laptop use so the chosen interval and actions do not sit below an enormous
player; exact size envelopes are frozen in the final reference and browser tests.
Retain the established 15 px body scale, operational typography, focus ring,
semantic errors, and 36 px fine / 44 px coarse-pointer target pattern.

## Final decisions

- Q10B: keep Review as the landing page, retaining its candidate-status default.
- Q11A: below 900 px, open a source as focused detail with Back to sources;
  the library is the initial Sources view. Wide layouts remain split.
- Q12: add All / Manual / Scored filtering alongside source and status filters.
  All groups manual segments first in creation order, followed by scored
  segments descending by score with stable tie-breaks. Handoff uses that same
  group order for the profile's entire selected set, independent of browsing
  filters; reveal selected counts hidden by filters.
- The selected visual artifact and explicit amendments are frozen in
  `reference-contract.md`; existing production style authority supersedes the
  comparison's standalone CSS.

## Delivery shape

One focused branch and one draft PR for #96, with coherent internal slices:

1. **Domain and shared operations:** explicit manual origin and genuinely absent
   model scores; backward-compatible library migration; profile-scoped manual
   creation; protect manual segments and saved human edits during re-scoring;
   adapt CLI/API/order/dedup/handoff to manual results without false scores.
   Pin existing scored-only behavior and Clipper schema-1 pickup compatibility.
2. **Process-lifetime batches:** serial worker, frozen profile/settings, observed
   stages/results, URL/path/file intake, stage-specific explicit retry, and Stop
   after current source. Each worker owns its library connection. Keep the web
   server responsive. Retain successful stages and safe custody; guard active
   resources against destructive cleanup. Reconnect by observing work rather
   than replaying mutations. No durable resumable queue or automatic paid retry.
3. **Production UI:** Sources/Review/Profiles, inline intake and advanced settings,
   batch strip, split inspector, transcript, manual timestamps, source filters,
   manual/scored presentation, cleanup confirmations, and existing handoff.
4. **Contract verification and integration:** production browser checks, CI
   enforcement, regression tests, documentation and tracking reconciliation.

After the first coherent green commit, create a draft PR and keep it current.
The complete integrated diff receives three cold reviews: correctness, contracts
(including visual/CI contracts), and standing skeptic. Reproduce and repair every
accepted in-scope finding with regression evidence; link explicitly approved
deferrals. Reviewers receive the frozen diff and governing contracts without the
implementation conversation or other reviewers' reports.

The requested gpt-6.1-sol xhigh agent runs implementation, reviews, and repairs
under an explicit ownership/authority packet. Parent integration, independent
acceptance, final UI inspection, documentation/tracking accounting, and CI-head
verification remain explicit before presenting a checked-out green-CI draft PR.
No ready, merge, release, live-library/profile use, posting, or actual Clipper
rendering is included in agent validation.

## Required automated evidence

- Domain/API/CLI tests: manual origin/no fake scores, migration preservation,
  source bounds, profile separation, reviewed/manual preservation, source-filter
  results, null-safe sorting and advisory dedup, handoff order and byte/metadata
  compatibility, and existing scored-only commands and contracts.
- Batch tests with injected adapters: serial admission, frozen profile/options,
  per-stage failures and retained success, failed-stage-only retry, stop boundary,
  read-only observation/reload recovery, missing record after process restart,
  no automatic replay, and destructive-operation concurrency/custody.
- Existing Node UI harness: stale responses/profile switches, duplicate submits,
  settings/intake modes, selection/filter behavior, manual creation, confirmations,
  and actual native-control wiring.
- Static style contracts: canonical sheet loads first, token roles are reused,
  layout spacing is derived from the named unit, and newly added rules do not
  introduce a copied theme. Prefer parsed declarations and rendered assertions
  over broad exact-text snapshots.
- Browser geometry/computed styles using production pages and intercepted or
  disposable fixture APIs: 320×800, 390×844, 768×1024, 899×800, 900×800,
  1024×768, 1366×768, 1440×900, and 1920×1080. Check page insets/gaps, split and
  narrow order, card rules, no horizontal overflow, full media framing, reachable
  actions, text/control style roles, and keyboard/coarse-pointer behavior.
- Change the spacing custom property in a browser fixture and verify derived
  geometry changes accordingly; matching one initial pixel value is insufficient
  proof that components use the constant.
- Exercise empty, long-title/transcript, active batch, failed scoring, unavailable
  media, manual/unscored, selected, and terminal handoff states. Capture wide and
  narrow screenshots as supporting evidence; assertions decide pass/fail.
- Add a required browser-check step to the existing Searcher gate job, using the
  pinned developer Playwright dependency and installed Chromium. Keep browser
  imports in suite test tooling, outside the Searcher production package. Missing
  browsers, unexpected skips, runtime errors, or missing scenarios fail the gate.
  Pin CI invocation in the existing workflow-contract tests. No new production
  dependency or blanket gate/coverage change.
- Run all existing applicable Searcher/suite gates and affected cross-pillar
  compatibility checks. Parent validates actual results and current-head CI;
  green self-reports or successful static mockup checks are insufficient.

All checks use temporary fixtures/profiles/media and mocked acquisition, ASR,
scoring, and embedding adapters. Browser media fixtures must exercise actual
native playback/seek controls without touching live libraries or accounts.
