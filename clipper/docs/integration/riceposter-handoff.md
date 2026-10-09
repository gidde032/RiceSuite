# RiceClipper → RicePoster handoff contract

**Status: implemented in RiceSuite.** RiceClipper writes completed batches to the
shared filesystem handoff after the maintainer clicks **Send to RicePoster**.
RicePoster stages a batch only after the maintainer clicks **Pull from Clipper**.
The Post tab may poll and display the inbox count, but polling is read-only: it
does not import a batch. The sender and receiver remain separate processes and
exchange local files only.

This contract covers RiceClipper → RicePoster. The current manual Send and Pull
decisions are recorded in the suite
[ADR-001 amendments](../../../docs/adr/ADR-001-ricesuite-consolidation.md).

## Goal and user flow

The filesystem handoff avoids manual export, rename, and re-upload while
preserving the human choices around when a batch leaves Clipper and when it
enters Poster:

1. In Clipper, review the whole batch and render its clips. A batch may arrive
   from Searcher through the separate Searcher → Clipper handoff, or the user
   may upload local media.
2. Click **Send to RicePoster** when the rendered batch is ready. Clipper does
   not send automatically after a render or another workspace action.
3. In Poster's Post tab, click **Pull from Clipper** when ready. That action
   stages the oldest complete batch into Review, captures a frame from each
   staged clip, and generates captions from each frame and its transcript using
   Poster's configured Clipper ingest style.
4. Review and edit the drafts, then choose **Post All** or **Schedule** through
   Poster's normal human gates.

The Pull action does not post or schedule. Clipper does not connect to Poster
and does not carry account, slot, or posting fields.

## Design principles

- **Filesystem pickup, not a Clipper API call.** Clipper writes local files and
  never contacts Poster's process or a posting surface.
- **Poster owns posting-side policy.** The active account roster, caption style,
  draft lifecycle, and posting decisions stay in Poster.
- **The manifest preserves clip context.** The reviewed transcript, burned-in
  header, and render presets travel as metadata. During an explicit Pull,
  Poster uses the transcript and a captured frame as caption inputs; the user
  reviews the generated caption in Review before posting or scheduling.
- **A complete batch is visible atomically.** The consumer ignores a batch until
  the producer has written `manifest.json`.

## Directory layout

Each batch has its own directory:

```
<handoff_root>/
  batch_20260826_1432_ab12/
    clip_1.mp4
    clip_2.mp4
    manifest.json        # written last — see atomicity below
```

The effective path depends on how RiceSuite is configured. A fresh Suite install
uses `<configured data root>/handoff/clipper-to-poster`, which defaults to
`~/.ricesuite/handoff/clipper-to-poster`. Suite sets Clipper's
`RICECLIPPER_HANDOFF_DIR` and Poster's `HANDOFF_DIR` to the same path. Existing
installations can retain the legacy `~/riceclipper-handoff` location until an
explicit data cutover. Standalone Clipper and Poster resolve an unset setting
the same way, so they also use that path; either side can be pointed
elsewhere, but both settings must resolve to the same directory. Run `rice data location` to inspect the
effective paths. See [RiceSuite data location and migration](../../../docs/data-migration.md)
before moving existing data.

### Atomicity

Clipper copies every rendered `clip_<position>.mp4` into a new batch directory,
then writes `manifest.json` via atomic rename. Poster ignores batch directories
without a manifest, so it cannot pick up a partially written batch.

## `manifest.json` schema

```json
{
  "schema_version": 1,
  "batch_id": "batch_20260826_1432_ab12",
  "created_at": "2026-08-26T14:32:00Z",
  "producer": "riceclipper",
  "clips": [
    {
      "file": "clip_1.mp4",
      "position": 1,
      "transcript": "full plain-text transcript of the clip",
      "header": "the on-screen header text the user typed",
      "presets": { "caption_style": "montserrat", "header_style": "plain" }
    }
  ]
}
```

- `position` preserves clip order. On Pull, Poster assigns clips in that order
  to the current active account roster. The manifest does not name an account,
  slot, caption style, or schedule.
- `transcript` is plain text that was reviewed in Clipper. Poster uses it as
  caption context alongside a frame captured from the staged video. A photo clip
  (SPEC D17) has an empty transcript, so its frame supplies the visual context.
- `header` is provenance; it is already burned into the video.
- `presets` is provenance for the rendered video and does not change Poster
  behavior. The field may be omitted.
- `schema_version` lets Poster reject an unknown future shape.

## Producer side — RiceClipper (implemented; manual Send)

After the batch is rendered and reviewed, the maintainer clicks **Send to
RicePoster**. The action calls `POST /api/handoff` (`app/handoff.py`) and writes
the batch to `RICECLIPPER_HANDOFF_DIR`. It copies the rendered clips in position
order, then writes the manifest last via atomic rename. Clipper only writes the
batch and does not manage its later lifecycle.

The send rules are part of the current contract: Send refuses a batch with an
unrendered, failed, or edited clip, and never sends only part of a batch. A
second send of a sent batch asks first. A successful Render all stops at the
send step and prompts the user to send when ready. These rules are recorded in
the [Manual Clipper send ADR amendment](../../../docs/adr/ADR-001-ricesuite-consolidation.md).

## Consumer side — RicePoster (implemented; manual Pull)

The Post tab polls `GET /api/handoff/inbox` to display waiting batches and an
inbox count. This endpoint is read-only. Only the maintainer's **Pull from
Clipper** click calls `POST /api/pull-from-clipper`; opening the page, an empty
workspace, a completed run, or inbox polling does not import a batch. Pull asks
before it overwrites unposted drafts.

A Pull stages the oldest complete batch, assigning its clips in `position`
order to the current active account roster. A batch with more clips than active
accounts is rejected. Poster records a durable receipt with the target accounts
and file hashes, then moves the source batch to its retained
`.riceposter-consumed/` archive. The browser applies the staged media to Review,
captures a frame from each clip, and generates captions through the existing
caption path using the transcript as topic and the configured
`CLIPPER_INGEST_STYLE` (public default: `generic`). Caption generation is part
of the user-started Pull flow; it does not start on page load or inbox polling.

The browser acknowledges the receipt after Review is prepared and caption
generation completes. If that flow is interrupted, another explicit Pull
replays the unacknowledged receipt and restores missing staged media. Poster
keeps acknowledged source archives until the user runs **Clear consumed
batches** in Local Media; that action removes only validated, acknowledged
archives. Clipper never deletes handoff batches.

After Pull, the user reviews the captions and drafts in Poster's normal Review
page and chooses **Post All** or **Schedule**. Handoff ingestion itself never
posts or schedules.

## Configuration

| Side | Setting | Meaning |
| --- | --- | --- |
| RiceClipper | `RICECLIPPER_HANDOFF_DIR` | Directory where Clipper writes batches |
| RicePoster | `HANDOFF_DIR` | Directory Poster reads; RiceSuite sets this to Clipper's same path |
| RicePoster | `CLIPPER_INGEST_STYLE` | Caption style used for clips pulled from Clipper; public default is `generic` |

For a fresh RiceSuite data root, the shared handoff path is
`<root>/handoff/clipper-to-poster` (default root: `~/.ricesuite`). A custom
`RICESUITE_DATA_DIR` changes that root. Existing installs may still use the
legacy path until cutover; use `rice data location` to check. In a standalone
setup, both handoff variables default to that same path. If overriding
either path outside RiceSuite, configure both sides to the same directory.

## Explicitly outside this contract

- Automatic sending from Clipper after render, edit, or other workspace events.
  Only **Send to RicePoster** creates a batch in the handoff.
- Automatic Poster import on page load, when Review is empty, or after a run.
  Inbox polling reports status only; only **Pull from Clipper** stages a batch.
- Posting or scheduling from Clipper or from the handoff consumer. Those actions
  remain behind Poster's human gates.
