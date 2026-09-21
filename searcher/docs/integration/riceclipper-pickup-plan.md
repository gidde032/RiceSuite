# RiceClipper "Pull from Searcher" consumer (Issue #8)

**Status: IMPLEMENTED in RiceClipper; handoff timing amended 2026-09-20.** This
documents the cross-repository contract. RiceClipper imports each handed-off file
unchanged, then independently transcribes it for authoritative caption and lyric
timing.

## What RiceSearcher now produces (the contract to consume)

**Directory topology (three distinct roots — RiceClipper is the intermediary):**

```
RiceSearcher --writes--> ~/ricesearcher-handoff/   (RICESEARCHER_HANDOFF_DIR)
RiceClipper  --reads --> ~/ricesearcher-handoff/    (this pickup — NEW)
             --writes--> ~/riceclipper-handoff/     (RICECLIPPER_HANDOFF_DIR, unchanged)
RicePoster   --reads --> ~/riceclipper-handoff/     (unchanged)
```

RiceSearcher and RicePoster **never share a directory.** RiceSearcher writes to its
own `~/ricesearcher-handoff` (default of `RICESEARCHER_HANDOFF_DIR`); this pickup
reads from that same dir; RiceClipper's existing rendered-output writer to
`~/riceclipper-handoff` is **unchanged**.

RiceSearcher writes a batch to `~/ricesearcher-handoff/`, mirroring the
RiceClipper→RicePoster *mechanism* (not its directory):

```
<ricesearcher-handoff>/
  batch_<ts>_<rand>/
    clip_1.mp4          # the exact reviewed [target_in, target_out] interval
    clip_2.mp4
    manifest.json       # written LAST via atomic rename = "batch complete"
```

`manifest.json` (producer `"ricesearcher"`, `schema_version` 1):

```json
{
  "schema_version": 1,
  "batch_id": "batch_20260903_..._ab12cd",
  "created_at": "2026-09-03T...Z",
  "producer": "ricesearcher",
  "clips": [
    {
      "file": "clip_1.mp4",
      "position": 1,
      "source_ref": "<url|path>", "source_title": "...", "published_at": "...",
      "source_window": { "pad_in": 10, "pad_out": 40, "target_in": 10, "target_out": 40 },
      "clip":         { "duration": 30, "target_in": 0, "target_out": 30 },
      "transcript": "...", "score": 0.8, "rationale": "...",
      "rights_risk": "low|med|high", "beat_profile_version": "..."
    }
  ]
}
```

- The clip file is exactly the reviewed interval. Schema 1 retains the historical
  `pad_*` keys, but all four `source_window` values describe the selected source
  bounds; clip-relative target is `0..duration`.
- `transcript` is rebuilt from Searcher's source words intersecting the reviewed
  interval. It is metadata, not caption timing: RiceClipper transcribes the exact
  imported file afresh and uses those clip-relative Whisper words for captions and
  pasted-lyric alignment.
- `position` (1-based) is the only routing/order signal. No account/slot/style —
  those are downstream posting-side policy, unchanged.

## RiceClipper behavior (mirrors RicePoster's consumer)

RiceClipper's delivered "Pull from Searcher" consumer mirrors RicePoster's pickup
discipline. Its input directory is `RICECLIPPER_SEARCHER_INBOX` (default
`~/ricesearcher-handoff`), which
must equal RiceSearcher's `RICESEARCHER_HANDOFF_DIR`. RiceClipper's **existing**
writer to `RICECLIPPER_HANDOFF_DIR` (`~/riceclipper-handoff`, for RicePoster) is
**unchanged** — this only adds a read side, making RiceClipper the intermediary.

1. **Scan** the searcher-inbox (`~/ricesearcher-handoff`) for batch dirs
   containing `manifest.json` (ignore manifest-less dirs — they're mid-write).
   **FIFO** by `created_at`, one batch per pull.
2. **Validate** the manifest: `schema_version == 1`, `producer == "ricesearcher"`,
   nonempty `clips`, a top-level object, string/safe `batch_id`, unique positive
   integer `position`s, string file/text fields — every malformed field → a
   controlled error and **zero** writes (RicePoster finding M-02).
3. **Dedupe by `batch_id`** with a durable consumed-ID registry so a batch is
   never ingested twice, including across restarts (RicePoster H-04). **Also be
   robust to the same source content arriving under two different `batch_id`s:**
   RiceSearcher's writer has an accepted residual two-phase gap (batch written to
   disk, then slices marked `handed_off` in a separate DB transaction — a crash
   between them lets a retry re-deliver the same clips as a new batch; see
   SPEC §7). So prefer a content-level idempotency signal (e.g. `source_ref` +
   `source_window`) in addition to `batch_id`, or surface likely-duplicate
   review cards rather than silently ingesting both.
4. **Durable custody before purge** (RicePoster H-02): copy each clip into
   RiceClipper's own working dir and persist a recoverable pending record
   (clip files + `clip.target_in/out` + transcript + provenance) **before**
   deleting the handoff batch. A response loss / reload must not orphan media.
5. **Ingest into RiceClipper's pipeline** as pre-cut clips and run its ordinary
   transcription/review/render flow. The complete Searcher metadata is retained,
   but RiceClipper does not import Searcher's transcript timings or trim the file.
6. **Path containment / safety** (RicePoster M-08): restrict `batch_id` and
   filenames to a strict safe format; resolve and verify every source/destination
   path stays within the intended roots; symlinks unsupported.
7. **Purge** the handoff batch dir only after **all** clips are in RiceClipper's
   custody; on any failure retain the whole batch for re-pull; partial deletion
   never happens (RicePoster purge policy).

## Verification

- A **paired-ref compatibility gate**: run RiceSearcher's real writer into
  RiceClipper's real consumer in disposable dirs; assert schema, positions,
  clip-relative window mapping, dedupe, FIFO, containment, and no writes outside
  owned roots (RicePoster M-10 lesson).
- Fail-before-fix regressions for malformed manifests, duplicate `batch_id`,
  response-loss custody, and containment.

## Out of scope / unchanged

- No posting. RiceClipper still performs no upload; the never-auto-post gate
  before RicePoster is unchanged.
- RiceClipper's roadmap edit retiring Path 1 / Path 2 extraction into
  RiceSearcher (Issue #9) is a separate doc change against that repo.
