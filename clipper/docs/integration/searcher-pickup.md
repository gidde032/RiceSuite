# RiceSearcher → RiceClipper pickup (consumer side)

**Status: implemented.** RiceClipper pulls batches of selected clips that
**RiceSearcher** wrote to its own handoff root, ingests each clip as a normal
review job, and the existing review → render → "Send to RicePoster" flow takes
over unchanged.

## Directory topology (RiceClipper is the intermediary)

```
RiceSearcher --writes--> <searcher-to-clipper>/   (RICESEARCHER_HANDOFF_DIR)
RiceClipper  --reads --> <searcher-to-clipper>/   (RICECLIPPER_SEARCHER_INBOX — this pickup)
             --writes--> <clipper-to-poster>/     (RICECLIPPER_HANDOFF_DIR — unchanged, to RicePoster)
RicePoster   --reads --> <clipper-to-poster>/     (unchanged)
```

RiceSearcher and RicePoster **never share a directory.** `RICECLIPPER_SEARCHER_INBOX`
must equal RiceSearcher's `RICESEARCHER_HANDOFF_DIR`. Unset, both are what
`rice data location` reports (`<root>/handoff/searcher-to-clipper`, or
`~/ricesearcher-handoff` on a legacy install), with or without `rice start`. RiceClipper still performs no posting — it reads local
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
  then "Send to RicePoster" writes the separate Clipper→Poster stage as before.

## Automatic pull, manual send (RiceSuite, ADR-001 Q12 as amended)

The review page polls `GET /api/searcher-inbox` (read-only, no job lock).
When a complete batch is waiting and nothing unsent would be displaced (the
workspace is empty, or it holds a batch that was fully rendered and sent), the
page performs the same pull as the button and starts transcribing. "Nothing
unsent" means the workspace holds exactly what was last sent; an edit or
re-render after sending holds it. A rendered batch that is not sent also
holds it. One Searcher batch is one Clipper batch. While a batch holds the
workspace, the page keeps reading the inbox and names the batch waiting behind
it on a line under the batch actions (RiceSuite #61). That line never pulls.
The **Pull** button follows the same rule, and only one pull runs at a time.
The page never sends a batch on its own (RiceSuite #59). Only a **Send to
RicePoster** click sends, after every clip has rendered. A failed or
unrendered clip, or one edited since its render, holds the batch until it is
rendered again or removed. The **Send** button refuses such a batch and names
the clips; it never sends part of a batch. Sending an already-sent batch again
asks first. Rendering stays a human action.

A pull removes the Searcher batch, so Clipper records it as **open** in its
work root until it is sent or discarded. `GET /api/workspace` returns an open
batch in the pull's shape (`?batch_id=` names one; else the oldest), and
`DELETE /api/workspace?batch_id=…` discards one (Start over, or removing every
clip). A pull takes a `pull_key`: a retry with the key of a pull whose reply
was lost gets the same batch back with `replayed: true`. Each tab keeps its
own pulled batch in `sessionStorage` and restores it after a reload; an open
batch the tab does not hold is named on the page and opens there only on a
**Pull** click.

For the RiceSuite home view, `GET /api/workspace-batches` returns a read-only
`{"batches": [{"batch_id": "…", "clip_count": 1}]}` summary of **all** open
batches, including one held after a failed render. It omits records whose jobs
have been removed. Unlike `GET /api/workspace`, this endpoint does not select
one batch for a tab or return job details.

`POST /api/handoff` takes a `send_key`: a retry with the key of a send that
already wrote its batch gets that batch back with `replayed: true`, and
nothing new is written; a key reused for other clips is refused (409). Each
clip goes to RicePoster once: a send that holds
a clip already sent under another key gets 409 with `already_sent`, unless it
carries `resend: true`, which the page sets only after the reviewer confirms a
second send.

## Idempotency note

RiceSearcher's writer has an accepted two-phase gap (it writes a batch to disk,
then marks its slices handed_off in a separate DB transaction), so the same source
content can occasionally arrive under two `batch_id`s. `batch_id` dedupe won't
catch that; a future enhancement could add content-level idempotency
(`source_ref` + `source_window`) or surface likely-duplicate cards. Low-stakes for
a human-driven, single-user flow.
