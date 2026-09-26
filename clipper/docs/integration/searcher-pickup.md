# RiceSearcher → RiceClipper pickup (consumer side)

**Status: implemented.** RiceClipper pulls batches of selected clips that
**RiceSearcher** wrote to its own handoff root, ingests each clip as a normal
review job, and the existing review → render → "Send to RicePoster" flow takes
over unchanged.

## Directory topology (RiceClipper is the intermediary)

```
RiceSearcher --writes--> ~/ricesearcher-handoff/   (RICESEARCHER_HANDOFF_DIR)
RiceClipper  --reads --> ~/ricesearcher-handoff/    (RICECLIPPER_SEARCHER_INBOX — this pickup)
             --writes--> ~/riceclipper-handoff/     (RICECLIPPER_HANDOFF_DIR — unchanged, to RicePoster)
RicePoster   --reads --> ~/riceclipper-handoff/     (unchanged)
```

RiceSearcher and RicePoster **never share a directory.** `RICECLIPPER_SEARCHER_INBOX`
(default `~/ricesearcher-handoff`) must equal RiceSearcher's
`RICESEARCHER_HANDOFF_DIR`. RiceClipper still performs no posting — it reads local
files here and writes local files to the separate RicePoster handoff.

## What RiceSearcher writes (consumed here)

One directory per batch under the inbox; `clip_<pos>.mp4` files (each the padded
window of a source) + `manifest.json` written **last** (atomicity signal):

```json
{ "schema_version": 1, "batch_id": "batch_...", "created_at": "...Z",
  "producer": "ricesearcher",
  "clips": [ { "file": "clip_1.mp4", "position": 1, "source_title": "...",
    "source_window": {...}, "clip": {"duration": 34, "target_in": 2, "target_out": 32},
    "transcript": "...", "score": 0.8, "rationale": "...", "rights_risk": "med",
    "beat_profile_version": "..." } ] }
```

The clip file **is** the padded window; `clip.target_in/target_out` are the intended
cut relative to the clip start. RiceClipper v1 does not trim (SPEC D5), so the
padded clip is reviewed/captioned/rendered as-is; the intended-cut metadata is
carried for future use.

## Behavior (`app/searcher_pickup.py`, `POST /api/pull-from-searcher`, "Pull from RiceSearcher" button)

- **Scan** the inbox for batch dirs containing `manifest.json` (manifest-less dirs
  are mid-write and skipped). **FIFO** by `created_at`, one batch per pull.
- **Dedupe** by `batch_id` via a durable `<inbox>/.riceclipper_consumed.json`
  registry, so a batch is never ingested twice even if its dir survives.
- **Validate** the selected manifest fully (schema_version 1, producer
  `ricesearcher`, safe `batch_id`, nonempty clips, unique positive integer
  positions, bare filenames contained within the batch dir); malformed → HTTP 400
  and **zero** jobs created.
- **Durable custody before purge:** each clip's bytes, probe result, and complete
  Searcher manifest metadata are written into its job directory before the source
  batch is removed. Imported jobs recover as `ready` after a server restart;
  Clipper still creates its own word timings and does not auto-trim the padded clip.
  Failed intake remains retryable without creating duplicate jobs.
- The ingested clips become normal review jobs; the human reviews and renders,
  then "Send to RicePoster" writes the separate `~/riceclipper-handoff` as before.

## Idempotency note

RiceSearcher's writer has an accepted two-phase gap (it writes a batch to disk,
then marks its slices handed_off in a separate DB transaction), so the same source
content can occasionally arrive under two `batch_id`s. `batch_id` dedupe won't
catch that; a future enhancement could add content-level idempotency
(`source_ref` + `source_window`) or surface likely-duplicate cards. Low-stakes for
a human-driven, single-user flow.
