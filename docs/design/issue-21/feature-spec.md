# Issue #21 — Searcher results and staged status bars

**Product and design direction: RATIFIED by Finn, 2026-09-30.**
Owner: [RiceSuite #21](https://github.com/gidde032/RiceSuite/issues/21).
The maintainer selected B from the comparison and explicitly approved the
Searcher restructure and the two Search/Clip status updates. They also stated:
"the design of clipper outside of the new progress bar will be untouched, the
clipper slots and new preview will stay the same, only the progress bar is
changing."

This specification consolidates that approval. The reference supplies concrete
spacing and responsive defaults derived from the selected preview. The bounded
implementation plan below was approved by Finn on 2026-10-01, satisfying
Clipper's operating rule 2.

## 1. Outcome

The maintainer can tell which clip is being processed, what stage it is in,
how many items have finished that stage, and what needs attention. Searcher's
wide review view also uses the available width to show two scored results per
row. These changes make everyday batch work easier to scan, without building
a separate monitoring product.

Existing application UI is the appropriate solution: an external monitor
cannot associate the existing review cards and handoff commit boundaries as
directly. There is no new dashboard, service, framework, or third-party design
system in this feature.

## 2. Scope and reference authority

- **Search:** widen the results workspace, use a responsive two-column grid,
  adapt card internals, and replace handoff status strings with B's condensed
  tracker. Participating cards display their handoff stage in the existing
  card status area, separate from validation/save errors.
- **Clip:** replace the pull/transcribe and render-all/send batch status
  presentation with the same condensed tracker. The existing per-clip status
  locations continue to provide clip detail; no new card footer is added.
- Genuine item-stage updates require small additions to progress reporting for
  Searcher handoff and Clipper pull/send. The rendering, transcription, custody,
  and transport algorithms are unchanged.

[Selected reference](progress-reference.html) owns the new status-bar anatomy
and Searcher grid geometry. The Clip tab intentionally depicts only the new
bar. It is not a replacement editor or slot design.

[Clipper's existing editor contract](../../../clipper/docs/design/editor-layout-spec.md),
`clipper/web/index.html`, and `clipper/web/style.css` own its slots, controls,
three-part upper row, rendered 9:16 frame, download controls, and text review.
No geometry, typography, size, color, component order, preview behavior, or
responsive behavior in those components is changed by this feature. The earlier
comparison's simplified Clipper editor is exploration evidence only.

The [shared Slate ownership](../slate-ownership.md) remains authoritative.
Palette reuse is already delivered; local status and layout rules belong to
the owning pages. Poster supplies a presentation reference only and receives
no changes.

## 3. Status-bar anatomy

B uses a compact full-width status surface in the pillar document, beneath its
toolbar and before review content. It never uses a sidebar or expanded list
above the cards. During Clipper's upload/intake state it is visible alongside
the intake workflow; during review it remains above the batch cards. Only one
operation summary is shown at a time, moving between stages as existing work
advances. Existing action buttons keep their position, behavior, and labels.

Finn explicitly accepted the bar above Clipper's editor on 2026-10-01,
including the short scroll to Download at some laptop sizes. The bar takes
normal document space; existing slot and preview geometry remains unchanged.

The bar contains:

1. **Operation**, such as Send selected to Clipper, Pull from Searcher,
   Render all, or Send to Poster.
2. **State**, expressed with both a mark and text: idle, working, ready,
   handing off, complete, failed/held, or connection lost/result unknown.
3. **Count**, with a stage-specific label: `1 / 3 prepared`, `2 / 3 ready`,
   `1 / 3 rendered`, or `3 / 3 handed off`. A generic percentage is not shown.
4. **Now doing**, naming the current clip and useful stage, or a final result
   and next action. For example: `Now: rendering clip 2 of 3 — captions,
   header & audio`.

Use the existing Slate tokens: backdrop #04060A, midground #0C1116, interior
#14191E, hairline #2B3136, control line #626A70, rice grey #AEB3B6, and primary
text #E5E8EA. Retain each page's existing semantic error red; no new status hues.
Use an 8 px radius, 1 px hairline border, 12 px horizontal padding and 10 px
vertical padding in Searcher. Clipper derives padding and gaps from its
existing `--editor-space: 8px` using 0.5, 1, or 2 multipliers, with 8 px padding.
Operation text is 14 px/600; stage/count/current-action text is 13 px/400,
line-height 1.5. Clipper operational text retains Arial; Searcher retains its
system font. Counts use tabular numerals. State marks never stand alone.

The operation, state, and count share a wrapping upper row; the current action
sits immediately below. Long filenames and errors wrap rather than truncate.
At narrow widths the fields wrap in reading order, with no horizontal overflow
or fixed height. The bar does not cover controls or introduce a fixed overlay.
There is no run-ID field, event log, expanding clip list, countdown, new cancel
action, or new retry button.

## 4. Stage and outcome contract

| Operation | Item stages | Completion means |
| --- | --- | --- |
| Searcher handoff | Waiting → preparing clip → prepared | The complete batch is committed and the handoff request reports success |
| Clipper pull | Waiting → importing clip → imported | The batch was pulled into existing durable job custody |
| Clipper transcription | Queued → transcribing → ready for review | The item is ready for human review, not rendered |
| Clipper render-all | Waiting → uploading music if applicable → rendering → rendered | That item has a successful render; batch may still be held |
| Clipper send | Waiting → copying clip → copied | Complete batch handoff is confirmed; clips wait in Poster's inbox |

Only real transitions produce updates. Do not simulate phases, infer a
percentage from elapsed time, add artificial delays, or expose internal ffmpeg
substeps. If a stage has no item identity yet, say `Finding the next batch…`
or `Importing batch…` without inventing an item or denominator. Pull's imported
count and transcription's ready count must not be conflated.

Prepared/copied clips are **not handed off** until the whole-batch publication
boundary succeeds. The bar explicitly changes to `Handing off batch…` while
that boundary is pending. Searcher library marking and response success remain
part of its existing handoff contract; if committed filesystem delivery and
library marking disagree, report that distinction rather than calling it a
confirmed failure with no batch. Observer code must not change commit order.

Automatic pull and automatic send use the same progress presentation as manual
actions. Successful render-all advances to the existing automatic send only
when every member is eligible under current freshness/sent-state checks.
The same bar then shows Send to Poster. Its successful final copy says the
batch is **waiting in Poster's inbox**, not posted or automatically ingested.

Failure handling follows existing semantics:

- A known preparation/import/copy failure names the item and reason. If the
  whole batch was rolled back, distinguish prepared work from delivery and
  show remaining items as not attempted. Do not report partial delivery.
- Render-all continues to other eligible clips as it does today. Preserve
  successful renders, show the failure count and failing clip in the summary,
  and keep automatic send held. Existing per-card errors retain their location
  and treatment; the bar supplies the compact batch outcome.
- A connection loss freezes last-known progress and labels it unconfirmed:
  work may still be running. Read-only reconnection may continue; neither a
  progress query nor a failed query may start/retry a write, render, or send.
- Existing keyed retries, lost-render-response recovery, resend confirmation,
  and concurrency guards continue to determine behavior. A retry begins a new
  visible attempt or reconnects to its existing operation as appropriate; it
  never mixes item identities or outcomes from two attempts.
- An unavailable operation after a server restart reports unavailable/unknown,
  not invented success or failure. The existing workspace recovery is separate.

## 5. Lifetime and interaction

Track the current operation and latest result, with a bounded temporary server
record where needed. The browser can reconnect while that same operation is
available; no durable run storage, permanent history, or server-restart recovery
is added. A refresh is not permission to resume or retry a mutation.

Keep a terminal summary until the next operation or an existing explicit reset.
Clipper Start over clears the bar under its existing guard. Searcher may
refresh its list/counts after a successful handoff as today; do not hold cards
in a filter they no longer match just to keep their progress visible. The final
summary survives that refresh. A profile/filter change cannot attach an old
profile's handoff status to newly displayed cards; any retained operation
summary identifies its original profile. A nonparticipating card gets no
operation status.

Progress polling belongs to the exact operation and participating item IDs,
not an ambiguous global latest batch. It stops after terminal completion or
explicit abandonment. A read must remain responsive during long processing,
and progress notification failures must never fail the underlying work.
Unrelated validation, profile-loading, cache, lyric alignment, and player errors
retain their own status surfaces.

Use `role="status"` and polite announcements for meaningful transitions and
terminal counts; do not announce every unchanged poll or every card at once.
Known actionable errors are announced once. Preserve keyboard focus and the
existing busy/disabled/inert gates during handoff and rendering. All source
titles, filenames, and error messages are rendered as text, never executable
markup.

## 6. Searcher review geometry

The results and status bar fill the available pillar-document width: remove
the 1,100 px cap. Use 16 px outer inset and a 16 px grid gap on desktop; at
720 px and below use 8 px inset and retain the existing wrapping toolbar.

At **900 px and above**, show exactly two equal-width cards per row. Below
900 px, use one column. Keep the descending score order and its existing tie
behavior in DOM, visual, and keyboard order: 1–2 / 3–4, never newspaper columns
or masonry. An odd last card occupies only the first column. Loading, empty,
and list-error messages span the full grid.

Keep the existing 8 px card radius, 16 px card padding, score/title treatment,
readable body size, preview framing, metadata, badges, transcript, reviewed
window controls, and Select/Reject actions. A card's content area of at least
500 px uses a preview/text split near 34/66; below that it stacks preview above
metadata. This changes layout, not the available information or source data.
Use a 12 px inner gap. Show the entire source frame with `object-fit: contain`;
do not crop video to fit the card. Text and controls wrap, with no shrunken text
or hidden badges. The reference's synthetic rank labels are demonstration
labels, not an additional required product field.

Searcher operation status occupies its existing per-card status area, outside
the editable transcript. Saved-window validation and mutation errors must not
be overwritten by an unrelated progress update. In-flight handoff retains
current busy/inert rules. Profile partitioning, target-window validation,
advisory duplicates, rights risk, scoring, and selection gates do not change.

## 7. Acceptance and verification

1. A slow, three-clip synthetic Searcher handoff reveals the actual active clip
   and completed preparation count before the final response. Confirm no
   handed-off state is shown before successful publication.
2. Synthetic Clipper pull/import, transcription, render-all, and manual/automatic
   send reveal their real coarse stages. The same UI covers existing automatic
   paths without additional automation.
3. One failing preparation prevents reported delivery; one failing render
   preserves other renders and holds auto-send. Cover a failure at publication
   and a lost response after publication separately.
4. Progress read failures do not fail work or retry mutations. Reconnecting
   uses the exact operation, with no stale cross-profile, cross-batch, or
   cross-tab item outcomes. Polls stop at terminal outcomes; unavailable
   post-restart progress has an honest unknown state.
5. Clipper card DOM/layout/style contracts remain unchanged, including the
   rendered preview's 9:16 shape, height, empty/busy/stale states, download
   behavior, and Music's equal transcript/lyrics panes. Compare existing
   fixture geometry before and after, not the simplified exploration mockup.
6. Browser checks at 390×844, 768×1024, 899×800, 900×800, 1024×768,
   1366×768, 1440×900, and 1920×1080 confirm column count/order, inner reflow,
   complete controls, readable/wrapping bar, and no horizontal overflow.
   Include long names, long errors, odd result counts, and empty results.
7. Existing relevant suites and per-pillar gates pass at their unchanged floors:
   Searcher 90%, Clipper 85%, suite boundary checks as applicable. Verify with
   fakes, temporary filesystem roots, and throwaway browser profiles only.
   No live application data, handoffs, credentials, or Instagram profiles.

## 8. Bounded implementation plan — approved 2026-10-01

1. **Reporting:** add small operation-scoped progress snapshots and coarse
   notifications at existing item boundaries in Searcher handoff and Clipper
   pickup/send. Reuse existing per-job/client states for transcription/render.
   Keep current request execution and commit/custody/idempotency behavior.
2. **UI:** implement the selected local bar markup/styles/rendering in Searcher
   and Clipper; update only Searcher's review layout. Preserve Clipper slot and
   preview DOM and CSS. No shared-CSS consolidation or Poster changes.
3. **Verification and docs:** prove intermediate updates and failure boundaries
   with synthetic slow operations, verify responsive geometry and unchanged
   Clipper components, run existing required gates, and reconcile the owning
   pillar specs/changelogs against the actual delivered behavior.

Implementation uses optional single-use observation IDs and read-only snapshots,
bounded to 64 records per pillar process. Searcher polls at 700 ms and Clipper
at 750 ms while active. Item notifications remain inside existing work
boundaries; observation reads do not take custody locks. These are implementation
details within the contract, not new product capabilities. Do not introduce a
background-job system, database table, durable
event bus, worker service, or new dependency for progress.

Root workflow remains Issue → focused branch → green commit → draft PR →
three cold reviewers and accepted repairs → maintainer-controlled readiness
and merge. No commit, push, PR, or implementation was performed in discovery.

## 9. Decision dispositions

All choices below are dated 2026-09-30 and owned by this feature contract.

- **RATIFIED:** informative per-clip stages; current operation/latest result;
  wider two-column Searcher review; B's compact summary and card detail;
  Clipper changes strictly confined to its progress presentation.
- **REJECTED for this increment:** A's expanded batch panel and C's side rail.
  Finn selected B to retain the review workspace and existing Clipper design.
- **DEFERRED outside #21:** detailed media percentages, a persistent progress
  history, and server-restart run recovery. Reconsider only if ordinary use
  demonstrates a need; no new delivery commitment or backlog item is implied.
- **REFERENCE LIMIT:** the prior A/B/C Clipper body and sample media were
  explanatory fixtures. They do not authorize changing the production editor.
