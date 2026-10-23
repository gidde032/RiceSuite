# Expanded Searcher workspace — visual discovery

Feature behavior: **RATIFIED 2026-10-22**, `SPEC.md §13`.
Visual layout: **Split workspace RATIFIED** by Finn with final Q10B, Q11A, and
Q12 amendments in `reference-contract.md`. The implementation plan is ratified.
Existing production styles take
precedence over the comparison's standalone appearance.
Owning delivery: [RiceSuite #96](https://github.com/gidde032/RiceSuite/issues/96).

## Fixed invariants

- Retain monochrome Slate and the suite shell. Searcher's navigation is Sources
  / Review / Profiles; Sources replaces Media.
- Preserve native video controls and current review/selection/handoff controls.
- Sources are shared; scoring and segments belong to an explicitly chosen
  profile. An in-flight batch retains the profile with which it started.
- One sequential processing batch at a time; inspection and review remain usable.
  Show actual stages and per-source outcomes, not invented stage percentages.
- Manual timestamps can be saved without scoring or automatic selection.
- Keep current CLI defaults and expose less common overrides in advanced settings.
- No new logo, palette, type identity, dashboard, or suite-level navigation.

## Design plan and brief review

Palette: backdrop `#04060A`, workspace `#0C1116`, input/card `#14191E`, divider
`#2B3136`, control boundary `#626A70`, rice `#AEB3B6`, text `#E5E8EA`; red is
reserved for errors and destructive actions. Retain the system-sans stack and
Arial operational text, with tabular numerals on durations/timestamps only.
Use restrained 6–10 px corners, compact native controls, and left alignment.
Separate batch-selection checkboxes from opening a source.

The full source and chosen interval are the workspace's center; the batch strip
remains independently visible. The brief review removed unrelated theme changes,
decorative thumbnails, KPI panels, custom playback chrome, and timeline editing.
Alternatives vary the source-library/detail relationship while holding theme,
sample content, and behavior constant.

## Layout comparison and selection

- **Split workspace** (SELECTED): persistent source list left, full-source
  inspector right, inline intake. Fast repeated switching; stacks when narrow.
- **Source detail** (not selected): full-width library opens a dedicated source detail view with
  Back to sources. More playback space, but leaves the list while inspecting.
- **Inspector drawer** (not selected): source list remains behind a wide right-side inspector.
  Compact while browsing, but adds overlay/focus work; becomes full-width on
  narrow screens.

## Selected details

- Add sources separates URL/path entry from picked/dropped files and offers
  explicit Add & score / Add only actions.
- A compact run strip expands to item results, names its original profile,
  and exposes retry/stop only when applicable.
- Review groups manual/unscored items separately from numeric ranking, retaining
  status/source filters and Select/handoff behavior.
- Advanced settings hold transcription/scoring models, offline mode, and the
  duplicate threshold near their actions.
- Errors and destructive confirmations remain inline and actionable.

## References and verification

- Editable comparison: `mockups/searcher-layouts.html` (inline fragment).
- Titles, profiles, transcripts, and results are synthetic. The native-player
  media fixture is generated for this mockup; no live library/account is used.
- Interactions demonstrate navigation, intake/settings, source inspection,
  timestamp saving, manual review, and run states without API calls.
- Rendered previews/browser checks are design QA, not production validation.
  Ratification must name the chosen layout and exact wide/narrow/empty/error
  reference states before implementation treats them as authoritative.

## Design state

Preserved: Slate, native controls, suite shell, ratified features/navigation.
Changed: source-library/detail placement across three proposed alternatives.
Selected: Split workspace and reuse of existing production styles/constants.
Settled: Review landing; narrow focused source detail with Back to sources;
All / Manual / Scored filtering with manual-first All order. Exact amendments,
geometry and mandatory reference/test states are in `reference-contract.md`;
delivery and mandatory coverage are in `implementation-plan.md`.

## Initial review checkpoint

The user requested a pause for review after the direction comparison was built.
The three layouts remain PROPOSED. JavaScript syntax, the fragment size/no-API
checks, and `git diff --check` passed. Native Chrome inspection confirmed the
split and source-detail layouts, a stacked narrow source view, timestamp editing
and saving into Review, native segment playback, source filtering/selection,
the intake/advanced-settings panel, and original-profile batch/stop copy.

Verification limits: exact 320 px overflow, the drawer's complete interaction
and focus behavior, all empty/error states, and synthetic batch-detail/count
transitions still need refinement before final visual ratification. This is a
layout comparison with local sample interactions, not a complete application.
No production code was changed and no production test/CI result is claimed.

## Style authority and regression requirement

Finn explicitly requires the existing stylesheet, spacing constants, and UI
patterns/quality, pinned by tests. The selected mockup governs information
placement and interaction intent; canonical Slate and existing production
components govern exact appearance. Normalize the final reference to that
hierarchy. Retain the comparison's alternatives as design evidence only.
