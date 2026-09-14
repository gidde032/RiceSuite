# RiceSearcher — v1 Specification

> **Status: RATIFIED 2026-09-02.** Boundary contract is `ADR-001.md` (ratified,
> reconciled against live sibling code 2026-09-02). This document is the
> implementation contract for the downstream decisions (D1–D8) the ADR routed
> forward. Implementation is authorized and proceeds by the phase ladder in §9,
> one owning GitHub Issue per phase.
>
> Project: **RiceSearcher** — the content-sourcing pillar of the Rice harness.
> Siblings: **RiceClipper** (render) and **RicePoster** (posting).

---

## 1. Vision

RiceSearcher kills the daily "where do I even look / which part do I cut"
bottleneck for a two-celebrity beat. On demand, it pulls source material,
transcribes it, scores which moments are clippable for the beat, and files them
as **scored candidate slices** in a local, moment-deduplicated **library**. A
human reviews and selects from that library (the ratified select-and-approve
gate), and selected slices are handed to RiceClipper as time-bounded,
padded-window clips. Success = a daily on-demand pull reliably surfaces a
reviewable queue of good moments, and selected moments flow to RiceClipper with
their transcript and provenance intact. Target supply: **3 unique pieces/day
(~90/month)**. Road goal: **L4** — automate up to the select gate; **never
auto-post**.

## 2. Boundary (from ADR-001)

RiceSearcher owns **what/when** to clip (discovery + *time*/trim), producing
scored candidate slices. RiceClipper owns **how it looks** (blur-pad-to-9:16 +
render). RicePoster posts. "Crop lives in Clipper" is satisfied for v1 by
RiceClipper's **blur-pad** normalization (ratified 2026-09-02); true
active-speaker reframe is a later reinvestigation. RiceSearcher **never posts,
publishes, or uploads** content anywhere — it reads sources and writes local
files only.

## 3. Ratified decisions (D1–D9)

| # | Decision | Settled as |
|---|----------|-----------|
| D1 | Discovery source & acquisition | **yt-dlp pull (URL/channel) + local watch-folder**, one thin acquisition layer → shared pipeline; search-query acquisition deferred to [#15](https://github.com/gidde032/RiceSearcher/issues/15) |
| D2 | Niche definition | Versioned **beat-profile**: NL brief + few-shot good/bad exemplars; many **saved profiles**, one JSON file each (D9) |
| D3 | Transcription ownership | **RiceSearcher owns it** via local faster-whisper; source captions optional, never depended on |
| D4 | Scoring | **Hybrid**: heuristic prefilter shortlists windows → LLM scores + explains the shortlist |
| D5 | Library store | **SQLite** slice index + **content-addressed disk cache** for source/clips |
| D6 | Moment dedup | Hybrid (intra-source time-overlap + cross-source transcript embedding), **advisory-only — never filters, discards, or blocks** |
| D7 | Interface | **CLI pipeline first**, then a minimal **Slate-styled** local review UI for the select gate |
| D8 | Handoff | RiceSearcher **writes** a mirrored filesystem handoff (manifest-last) with a **superset schema**; Clipper pickup is routed-forward |
| D9 | Saved profiles | Profiles are JSON files in `<data_dir>/profiles/`; id = file stem; library, dedup, review, and handoff **partition by `profile_id`**; sources shared; every scoring run names its profile; legacy rows adopt `example-beat` (ratified 2026-09-14, [ADR-002](ADR-002.md), [design spec](docs/design/profiles-spec.md)) |

## 4. Functional requirements

- **FR-1 — Acquire (D1).** `pull` accepts a YouTube URL/channel (via yt-dlp)
  **or** ingests a file dropped in the local watch-folder. Both
  produce a normalized source record + cached media in the content-addressed
  store. Acquisition never posts or authenticates to any posting surface.
- **FR-2 — Transcribe (D3).** Each acquired source is transcribed locally with
  faster-whisper to word-level timestamps pinned to the source timeline in
  seconds. Source captions may seed a fast pass but are never required.
- **FR-3 — Shortlist (D4).** A heuristic prefilter scans the transcript and emits
  candidate windows (entity mentions, turn/laughter/sentiment/quotable signals)
  with cheap feature scores. The LLM never sees the whole transcript.
- **FR-4 — Score (D4, D2).** An LLM scores + explains each shortlisted window
  against the versioned beat-profile, producing a clippability score (0–1) and a
  short rationale. The `profile_id`, beat-profile version, and model id are
  recorded on the slice. A scoring run names one profile (D9); a re-score
  replaces candidate rows of that profile only.
- **FR-5 — Store scored slices (D5, ADR Q3/Q4b).** Each scored candidate slice is
  written to the SQLite library with the schema in §6, including a **padded
  window** and the **intended in/out** as metadata (not a final cut). Rows are
  partitioned by `profile_id`; one source may hold slices under several profiles.
- **FR-6 — Dedup signal (D6).** For each new slice, compute an intra-source
  overlap check and a cross-source transcript-embedding similarity; attach a
  **"possible duplicate" annotation** (with the matched slice id + score) when
  above threshold. This annotation **never** removes, hides, blocks, or
  deprioritizes the slice; it is display metadata only. Dedup compares slices
  inside one profile only (D9).
- **FR-7 — Inspect via CLI (D7).** CLI commands list/show library sources and
  transcripts, and list scored slices with an optional source filter. `score`,
  `slices`, `dedup`, and `handoff` require `--profile`; `profiles` lists every
  profile with its counts (D9). Detailed
  slice review, status filtering, rationale, and duplicate context belong to the
  review UI (FR-8), not a parallel CLI surface.
- **FR-8 — Review & select (D7, ADR Q5).** A local web review UI (Slate design
  system) shows candidate moments (thumbnail + transcript span + score +
  rationale + duplicate annotation), lets the human tighten the intended in/out
  by eye, and **select** slices for handoff. This is the human gate. The UI
  shows one profile at a time: a profile select in the topbar and a Profiles
  page listing name, version, sources, candidates, and selected per profile
  (D9). A slice whose version differs from its profile file is marked stale.
  - **FR-8a — Media management.** The UI includes a media-management page listing
    every stored source (url, cached media, size, slice count) with a per-source
    **delete** and a whole-cache **clear**. These are **full-purge** (maintainer-
    ratified 2026-09-10): deleting a source cascades to its transcript and *all*
    its candidate slices — including `selected` and `handed_off` rows — and
    unlinks the on-disk media once no other source references it. This is
    **local-only** (SQLite rows + local files; no external surface) and is the
    one deliberate exception to §7's "durable custody before any purge" lesson:
    it destroys *library* state, never a written handoff batch (those stay
    producer-write-only, and a re-pull re-delivers under a new `batch_id`). The
    destructive controls are gated by a two-step confirm in the UI; because the
    API endpoints themselves are unguarded, the review server must stay bound to
    `127.0.0.1` (its default) — do not expose it on a shared interface.
- **FR-9 — Write handoff (D8, ADR Q4b).** On select, write a filesystem handoff
  batch to the shared root: `clip`/source media + `manifest.json` written **last**
  as the atomicity signal, with the superset schema in §7. A batch holds the
  selected slices of one profile and carries `profile_id` per clip. Producer only ever
  writes; it never deletes or ingests. Batch identity is stable and idempotent.
- **FR-10 — Safety.** No network call posts, publishes, or uploads content. The
  only outbound calls are source acquisition (yt-dlp fetch) and the scoring LLM
  API; neither touches any account, platform, or posting surface.

## 5. Non-functional budgets (initial targets — calibrate against the walking skeleton)

> No code exists yet, so these are **targets to measure and ratchet at Phase 1**,
> not floors the tree currently meets. Bootstrap rule: set the real numbers by
> measuring the skeleton before wiring them as gates.

- **Coverage floor:** target ≈ 80% on core extraction/scoring/dedup/handoff logic;
  the *enforced* floor is set just under the measured skeleton coverage at Phase 1
  and ratcheted up. (Siblings: RicePoster 43%, RiceClipper 85%.)
- **Scoring cost:** LLM sees only shortlisted windows; target **≤ N tokens per
  30-min episode** — N fixed after the Phase-2 skeleton measures real shortlist size.
- **Pull latency:** on-demand pull of one ~30-min source completes transcription +
  scoring within a target measured at Phase 2 (dominated by faster-whisper on CPU).
- **Local-first:** no cloud storage; SQLite + local disk only. Media cache is
  content-addressed and de-duplicated on disk.
- **Handoff atomicity:** a reader never sees a partial batch (manifest-last
  guarantee); a failed write leaves no pickup-visible artifact.

## 6. Library — candidate-slice schema (D5, SQLite)

One row per candidate slice (finalized at Phase 2). **Provenance is normalized:**
the slice row stores only `source_id` (a foreign key into `sources`); the full
provenance (`kind`, `ref`, `title`, `channel`, `published_at`, `acquired_at`,
word-level transcript) is recovered by joining `sources` / `transcript_words`
rather than copied onto every slice. The handoff writer (Phase 5) resolves these
at write time.

- `id` — stable slice id (deterministic from `source_id` + `profile_id` + the
  intended window: `{source_id}:{profile_id}:{in_ms}-{out_ms}`).
- **Provenance:** `source_id` (FK → `sources`).
- **Profile (D9):** `profile_id` (file stem of the profile; legacy rows carry
  `example-beat` after the schema v3 migration).
- **Window (ADR Q4b):** `pad_in`, `pad_out` (padded window, seconds on source
  timeline) and `target_in`, `target_out` (intended in/out — metadata, tightenable
  at review, *not* a final cut).
- **Content:** `transcript_span` (text), `transcript_words` (word timings ref).
- **Score (D4):** `score` (0–1), `rationale`, `heuristic_features`,
  `beat_profile_version`, `scorer_model`.
- **Dedup (D6):** `dup_of` (matched slice id | null), `dup_score`, `dup_kind`
  (`intra`|`cross`) — advisory only.
- **Rights (ADR Q3):** `rights_risk` (low/med/high + note).
- **Lifecycle:** `status` (`candidate`|`reviewed`|`selected`|`handed_off`|
  `rejected`), timestamps.

Media bytes (source + any extracted preview/clip) live in a content-addressed
cache dir keyed by hash, referenced by the row — never inlined in SQLite.

## 7. Handoff — Searcher→Clipper contract (D8, seeds from Phase 1)

Mirrors the RiceClipper→RicePoster **mechanism** (see
`RiceClipper/docs/integration/riceposter-handoff.md`) — **not its directory**.
RiceSearcher writes to its **own** handoff root, `RICESEARCHER_HANDOFF_DIR`
(default `~/ricesearcher-handoff`), which RiceClipper reads from; RiceClipper's
rendered output goes to the **separate** `~/riceclipper-handoff` (where RicePoster
pulls). RiceSearcher and RicePoster never share a directory — RiceClipper is the
intermediary. The mechanism: one directory per batch; media files + `manifest.json`
written **last** via atomic rename as the completeness signal; FIFO by `created_at`;
dedupe by stable `batch_id`; **producer only writes** and never manages lifecycle.

**Superset manifest schema** (adds what Clipper's already-cut-clip manifest lacks):

```json
{
  "schema_version": 1,
  "batch_id": "batch_<ts>_<rand>",
  "created_at": "<ISO8601Z>",
  "producer": "ricesearcher",
  "clips": [
    {
      "file": "clip_1.mp4",
      "position": 1,
      "source_ref": "<url|path>", "source_title": "...", "published_at": "...",
      "source_window": { "pad_in": 0.0, "pad_out": 0.0, "target_in": 0.0, "target_out": 0.0 },
      "clip":          { "duration": 0.0, "target_in": 0.0, "target_out": 0.0 },
      "transcript": "plain-text transcript span",
      "score": 0.0, "rationale": "...",
      "rights_risk": "low|med|high",
      "beat_profile_version": "...",
      "profile_id": "..."
    }
  ]
}
```

The clip file **is** the padded window, so `source_window` is provenance on the
source timeline and `clip.target_in`/`target_out` are the intended cut **relative
to the clip's start** (`duration = pad_out − pad_in`) — the boundary Clipper
tightens around.

**Consumer:** RiceClipper has **no pickup side** today (it ingests via its upload
UI). A "Pull from Searcher" consumer is a **routed-forward cross-repo item**
(Issue #8), planned in
[`docs/integration/riceclipper-pickup-plan.md`](docs/integration/riceclipper-pickup-plan.md).
Until it ships, the maintainer bridges selected clips into Clipper manually.

> **Residual two-phase gap (accepted).** The handoff writes the batch to disk,
> then marks the slices `handed_off` in one DB transaction — but there is no
> transaction spanning the filesystem write and the DB mark. If the process dies
> strictly between them, a complete batch exists on disk while the slices remain
> `selected`, so a retry re-delivers them as a **new** `batch_id`. The window is
> tiny (a human-driven action) and both are individually correct; the consumer
> should therefore be robust to the same source content arriving in two batches
> (content-level idempotency, not only `batch_id` dedup).

**Integration-ledger lessons applied up front** (from
`RiceClipper/internal/riceposter-integration-review.md`): stable idempotent batch
identity (a render/selection revision creates the id once; retries reuse it);
durable custody before any purge; manifest-last atomicity; strict `batch_id` /
filename safe-format + resolved path containment (symlinks unsupported);
whole-batch default with explicit, named partial; a paired-ref compatibility
gate when the consumer is built.

## 8. Design / module boundaries

- `acquire/` — yt-dlp adapter + watch-folder ingest → normalized source record.
- `transcribe/` — faster-whisper wrapper (pattern reused from RiceClipper).
- `extract/` — heuristic prefilter → candidate windows.
- `score/` — beat-profile loader + LLM scorer.
- `library/` — SQLite store + content-addressed media cache + dedup.
- `handoff/` — batch writer (manifest-last, superset schema, containment).
- `cli/` — pull/list/inspect/select commands.
- `web/` — Slate-styled FastAPI + vanilla-JS review UI (Phase 4).
- Stack: Python + FastAPI, faster-whisper, yt-dlp, a local embedding model for
  dedup, Anthropic API for scoring. Local-first throughout.

## 9. Phase outline (each ends in a usable increment)

- **Phase 1 — Acquire + transcribe skeleton.** `pull <url|file>` → cached source +
  word-level transcript in the SQLite library. *Usable:* pull a source and read
  its transcript. (Walking skeleton — measure budgets here.)
- **Phase 2 — Extract + score.** Heuristic prefilter → LLM scoring against the
  beat-profile → scored candidate slices with padded + intended in/out. *Usable:*
  a pull yields scored, inspectable moments via CLI.
- **Phase 3 — Dedup signal.** Advisory possible-duplicate annotation (intra-source
  + cross-source embedding). *Usable:* the library flags near-dupes without ever
  filtering them.
- **Phase 4 — Slate review UI + select gate.** Local web UI to browse, tighten
  in/out, and select. *Usable:* visually review and select moments.
- **Phase 5 — Handoff writer.** On select, write the mirrored superset handoff
  batch to the shared root. *Usable:* selected slices land as a handoff batch.

## 10. Risks

- **Taste (routed-forward spike).** Whether LLM segment-scoring picks
  niche-clippable moments well enough to trust. A prototype on real episodes gates
  any move toward auto-select; v1 keeps the human select gate regardless.
- **Rights.** Source material is copyrighted; `rights_risk` is tracked per slice,
  content stays off any public surface, repo is private.
- **Transcription cost/latency.** faster-whisper on CPU for long podcasts;
  measured and budgeted at Phase 1–2.
- **Consumer gap.** Clipper can't yet ingest the handoff; v1 produces the artifact
  and the maintainer bridges manually until the routed-forward pickup ships.
- **yt-dlp fragility.** Source-site changes can break acquisition; the local
  watch-folder is the always-available fallback door.

## 11. Open questions (deferred, labeled)

- **Taste spike** (routed forward) — design it as a prototype; gates auto-select.
- **Scheduled-monitor watcher** (ADR Q2) — future-enhancement Issue; on-demand
  pull is the v1 foundation.
- **Search-query acquisition** — deferred to
  [Issue #15](https://github.com/gidde032/RiceSearcher/issues/15) as the first
  post-v1 feature; v1 accepts YouTube URLs/channels and local files.
- **RiceClipper "Pull from Searcher" consumer** — routed-forward cross-repo item.
- **RiceClipper roadmap edit** — retire Path 1/Path 2 extraction into RiceSearcher
  (ADR Action Item #4), against the real Clipper repo with approval.
- Final numeric budgets (coverage floor, scoring token cap, pull latency) —
  calibrated at Phase 1–2 against the skeleton.

## 12. Definition of done (v1)

A working prototype for the maintainer's own daily use: `pull` (URL or local
file) reliably produces scored, moment-annotated candidate slices in the library;
the Slate review UI surfaces them for select-and-approve with tightenable in/out;
selected slices are written as a valid, atomic handoff batch with transcript +
provenance; format/lint/type/tests-with-coverage-floor gates are green; and no
code path can post, publish, or upload content. Not hardened for external users.
