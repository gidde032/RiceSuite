# RiceClipper → RicePoster handoff contract

**Status: contract RATIFIED. Producer side (RiceClipper writer) IMPLEMENTED;
consumer side (RicePoster pickup) PENDING in that repo.**
This is Wave-1 item #1 (`ROADMAP.md`). The producer half — writing a batch of
clips + a manifest into the handoff directory — is implemented in RiceClipper
(`app/handoff.py`, `POST /api/handoff`). The consumer half (RicePoster "Pull
from Clipper") is a separate effort in the `clippingharness`/RicePoster repo
under its own approval.

## Goal

Let a day's clips flow from RiceClipper to RicePoster with no manual export,
rename, or re-upload — while preserving exactly three human touchpoints:

1. **[manual]** upload clips to RiceClipper → auto batch-transcribe
2. **[manual]** review transcripts + headers, approve render → auto-render → auto-handoff
3. *(auto)* RicePoster ingests, assigns slots, writes captions
4. **[manual]** final caption review → **Post All**

## Design principles

- **Filesystem pickup contract, not an API call.** RiceClipper only ever *writes
  local files*. It never opens a connection to the posting tool and carries no
  account, slot, or posting fields. This honors Hard Rule #1 and SPEC §3, keeps
  either tool usable standalone, and matches both repos' existing intent
  (RiceClipper's "pickup contract"; RicePoster's on-disk `queue_media/`
  snapshot model).
- **RicePoster owns all posting-side policy** — slot assignment, caption style,
  caption generation, lifecycle after pickup.
- **Transcript is the payload's value.** RiceClipper already has each clip's
  word-level transcript — a far richer caption-grounding input than a single
  frame or a typed topic, and one RicePoster cannot produce itself. It travels
  in the manifest so RicePoster can generate content-grounded captions with zero
  typing.

## Directory layout

A single shared **handoff root** (configurable path on both sides; suggested
default `~/riceclipper-handoff/`). RiceClipper writes; RicePoster reads.

```
<handoff_root>/
  batch_20260826_1432/
    clip_1.mp4
    clip_2.mp4
    clip_3.mp4
    manifest.json        # written LAST — see atomicity below
```

### Atomicity

RiceClipper writes all `clip_N.mp4` files first and writes `manifest.json`
**last**. RicePoster ignores any batch directory that has no `manifest.json`, so
it can never read a half-written batch. No separate lock/marker file is needed.

## `manifest.json` schema

```json
{
  "schema_version": 1,
  "batch_id": "batch_20260826_1432",
  "created_at": "2026-08-26T14:32:00Z",
  "producer": "riceclipper",
  "clips": [
    {
      "file": "clip_1.mp4",
      "position": 1,
      "transcript": "full plain-text transcript of the clip",
      "header": "the on-screen header text the user typed",
      "presets": { "caption_style": "classic", "header_style": "plain" }
    }
  ]
}
```

- `position` (1-based) is the **only** routing signal. It defines pickup order;
  RicePoster maps it to a slot. There is deliberately no `slot`, `account`,
  `style`, or `schedule` field — those are posting-side policy.
- `transcript` is plain text (already user-reviewed at touchpoint 2). Grounds
  caption generation.
- `header` is provenance only (already burned into the mp4); RicePoster may show
  it but does not act on it.
- `presets` is provenance only (already baked into the render). It records which
  RiceClipper visual presets produced the clip; it never crosses the boundary as
  behavior. Field may be omitted.
- `schema_version` lets the consumer reject an unknown future shape loudly.

## Producer side — RiceClipper (IMPLEMENTED)

After a batch is rendered, the review UI's **"Send to RicePoster"** button posts
the done clips to `POST /api/handoff` (`app/handoff.py`). For each clip, in
handoff order, it copies the rendered mp4 to `clip_<position>.mp4` under a fresh
`batch_<ts>_<rand>/` in the handoff root, then writes `manifest.json` last via an
atomic rename. The handoff root is `RICECLIPPER_HANDOFF_DIR` (default
`~/riceclipper-handoff`). The client sends the reviewed transcript text, so no
server-side batch object is needed. RiceClipper only writes here and does not
manage the batch afterward; it is not the lifecycle owner.

## Consumer side — RicePoster (PENDING — its own repo's authorization)

A "Pull from Clipper" action (or a light watcher) scans the handoff root for
batch dirs containing a `manifest.json`.

**Selection order — oldest first (FIFO).** When multiple ready batches are
present, RicePoster pulls the one with the oldest `created_at` / `batch_id`
timestamp, one batch per pull. This keeps the day's clips in the order they were
produced and makes a backlog drain predictably rather than in scan order.

For each new `batch_id` (dedupe by `batch_id` so a batch is never ingested
twice):

1. For each clip in ascending `position`, take the next slot from `SLOT_IDS`
   (positional A/B/C…). Slots are interchangeable for the maintainer's use, so
   ordinal assignment is sufficient.
2. Copy the mp4 into `media/{slot}_{file}` (RicePoster's existing convention).
3. Generate a caption using the configured default style — **`default-style`** —
   with the clip's `transcript` as the content description. Style remains
   overridable per the existing UI dropdown before Post All.
4. Assemble a pending run and surface it in the normal review UI.

**Post All is unchanged** — the human reviews captions and posts as today.

### Lifecycle / cleanup ownership

RicePoster owns the batch after a successful pull (mirroring its `queue_media`
snapshot philosophy). RiceClipper never deletes a batch from the handoff root —
it only ever writes.

**Purge policy — remove only after all clips ingest successfully.** A batch dir
is deleted from the handoff root only once **every** clip in it has been
successfully ingested into RicePoster's own custody (copied into `media/`, and
into the `queue_media/` snapshot for a scheduled batch). At that point the
handoff copy is redundant — RicePoster holds its own copy and drives the rest of
the lifecycle. If any clip in the batch fails to ingest, the **entire** batch dir
is retained so the whole batch can be re-pulled without re-rendering; partial
deletion never happens. Between pull and confirmed full ingest, `batch_id`
dedupe prevents a re-pull from double-ingesting.

> Note: "ingested" here means safely copied into RicePoster's custody, not
> "posted." RicePoster already retains its own snapshot until every slot posts
> (its existing `queue_media` guarantee), so retaining the handoff copy through
> posting too would be redundant. If you'd rather the handoff copy survive until
> posting is confirmed, that is a one-line change to this trigger.

## Style default

The daily workflow is built around the `default-style` caption style. RicePoster
applies it as the pickup default via config (e.g. a handoff-ingest style
setting), not hardcoded, so it can change later without a code edit. RiceClipper
never learns what `default-style` is — style is entirely a posting-side concern.

## Configuration

| Side | Setting | Meaning |
| --- | --- | --- |
| RiceClipper | handoff root path | Where batches are written |
| RicePoster | handoff root path | Where batches are read |
| RicePoster | default ingest caption style | Defaults to `default-style` |

## Explicitly out of this contract

- Auto-trigger (RiceClipper pinging RicePoster to skip the "Pull" click) —
  optional Phase 3, layered on this same contract without changing it.
- Scheduling of pulled batches — RicePoster's existing concern, unchanged.
- Any RiceClipper awareness of accounts, platforms, or posting outcomes.
```
