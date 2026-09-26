# Triage report — post-merge review of PR #27, #29, #33

**Type:** independent multi-agent review (agentic-review-orchestration), union audit
of three already-merged PRs as they now live on `main`.
**Frozen target:** `main` @ `576c73c` (PR33 merge). Per-PR ranges —
PR27 `338540d..5eaef8d`, PR29 `dceb3e1..c75892a`, PR33 `ca72396..4171081`.
**Date:** 2026-09-21.
**Authority note:** the review ran with no repair authority. The maintainer then
authorized the recommended repairs for A, B1/B2, C, E and the D cleanup
(2026-09-21); those are now applied — see **Resolution** below. The findings text
is preserved as-written for the record.

## How this was produced

Five isolated cold reviewers against the frozen diffs, none seeing the others,
the PR descriptions, or `handoff.md`:

- **R1 — skeptical senior engineer** (Opus)
- **R2 — storage / data-integrity & handoff-atomicity specialist** (Opus)
- **R3 — interval-correctness / Clipper-contract + docs-drift** (Sonnet)
- **R4 — OCR delegate reviewer**, narrow scope: concurrency / resource / error-handling (Sonnet)
- **PG — OCR pre-gate**, broad rule set (Sonnet); a pre-pass, not counted as an independent reviewer.

OCR pre-gate ran locally only (v1.12.7); **no automated comments were posted to
the merged PRs**. All reviewer file:line evidence was re-verified by the parent
against `576c73c` before inclusion. Convergence counts only Claude-vs-OCR or
Claude-vs-Claude agreement (the two OCR passes agreeing is the same engine and
does not count).

## Summary

| # | Finding | Sev | Convergence | Suggested disposition |
|---|---------|-----|-------------|----------------------|
| **A** | Handoff holds the SQLite write lock across the entire ffmpeg encode → concurrent review write 500s | **High** | R2 (Claude) + PG + R4 (OCR) — strong | Repair (options below) |
| **B1** | Exported clip can be silently shorter than the selected window near source end; manifest still reports it as exact | **High** | R3 — single-path, verified true | Repair (options below) |
| **B2** | One over-duration slice 409s the *entire* handoff batch; `target_out` never bounded at scoring | **Medium** | R1 (root-cause convergence with B1) | Repair or defer |
| **C** | `extract()` raises bare `RuntimeError`; `do_handoff` doesn't catch it → 500 instead of the intended 503 | **Low–Med** | R4 — verified true | Repair (fold into A) |
| **D** | `HandoffEntry.pad_in/pad_out` are now write-only/dead; silent no-op for a future editor | **Low** | PG — verified true | Defer / cleanup |
| **E** | `PROFILE_ID_PATTERN` lost its `^…$` anchors; safe today, unsafe-by-default for future `.match()` callers | **Low** | PG (R1 corroborated: no live defect) | Defer / cleanup |

No data-corruption or wrong-account/publishing defects were found. Migration
atomicity, PR29 path-equivalence, lifecycle/terminal-state guards, manifest-last
atomicity, and the transcript-overlap rebuild were each independently inspected
and found clean (details at the end).

B1 and B2 are two symptoms of **one root cause** and are cheapest to fix together.

---

## Resolution (applied 2026-09-21)

All six items are repaired in branch `fix/review-27-29-33-repairs`
([PR #34](https://github.com/gidde032/RiceSearcher/pull/34)) with fail-before-fix
regression evidence. Full gates green: `ruff format --check`,
`ruff check`, `mypy` (33 files), `pytest` 244 passed at 93.90% coverage (floor 90),
`node --check`, 17 Node tests, 5 smoke tests.

| # | Disposition | Regression (fail-before-fix) | Key files |
|---|-------------|------------------------------|-----------|
| **A** | **Repaired** — `hand_off_selected` encodes outside the write lock; a short `immediate_transaction` re-reads and marks only if the snapshot is still exactly selected (deliver-exactly-once preserved). Companion 503 mapping via C. | `test_a_concurrent_review_write_not_blocked_during_handoff` (pre-fix: `OperationalError: database is locked`) | `handoff/writer.py` |
| **B1** | **Repaired — option 2 (record measured).** `extract()` returns the measured output duration; the manifest records measured length/window, never overstating. Chosen over option 1 (reject) to avoid re-introducing batch-poisoning; can switch to fail-closed reject on request. | `test_b1_manifest_records_measured_not_requested_duration` | `handoff/extract.py`, `handoff/writer.py` |
| **B2** | **Repaired — option 1 (clamp at handoff).** `target_out` clamped to the verified source duration instead of 409-ing the batch; a window at/after source end still fails closed. | `test_b2_target_out_past_source_is_clamped_not_batch_rejected` | `handoff/writer.py` |
| **C** | **Repaired — option 1.** New `ClipExtractError`; `do_handoff` maps it to a graceful 503 (retryable), not a raw 500. | `test_c_unusable_clip_returns_503_not_500` | `handoff/extract.py`, `web/app.py` |
| **D** | **Repaired.** Removed the dead `HandoffEntry.pad_in/pad_out` fields (manifest keys unchanged, sourced from `target_*`). | covered by existing handoff manifest tests | `handoff/writer.py` |
| **E** | **Repaired.** Re-anchored `PROFILE_ID_PATTERN` with `\A…\Z` (safe-by-default). | covered by existing profile-id tests | `beat/profile.py` |

Housekeeping (`upsert_slices` possibly test-only): **not actioned** — left as a note
for a future `store.py` touch, per the report.

Docs reconciled to the new behavior: `SPEC.md` §7 + D8, `ADR-001.md` Q4/trade-offs,
`README.md` review workflow, `docs/integration/riceclipper-pickup-plan.md`.

---

## A — Handoff write lock spans the ffmpeg encode → concurrent 500 *(High)*

**Where:** `ricesearcher/handoff/writer.py:220-263`, `ricesearcher/library/store.py:116`,
`ricesearcher/web/app.py:242,296,317`.

**What:** `hand_off_selected` opens `library.immediate_transaction()`
(`BEGIN IMMEDIATE`, store.py:167-178) at writer.py:220 and holds it across the
whole clip loop, which calls `extractor.extract(...)` — a blocking `ffmpeg`
re-encode per selected clip (extract.py:72) plus an `ffprobe` per source. The
connection is opened with `sqlite3.connect(self.db_path)` and **no `timeout=`**
(store.py:116), so SQLite's default 5.0 s busy timeout applies.

**Trigger → effect (verified):** each web endpoint opens its own short-lived
`Library` connection. While a handoff of more than one or two clips is encoding
(a `libx264 -preset veryfast` batch routinely exceeds 5 s in aggregate), a
concurrent write from another browser tab — `POST /api/slices/{id}/status`
(app.py:242) or `PATCH /api/slices/{id}/window` (app.py:296) — or a second-process
CLI `ricesearcher handoff`, blocks on the RESERVED lock, times out, and raises
`sqlite3.OperationalError: database is locked`. No endpoint catches it, so it
surfaces as an opaque HTTP 500. `_handoff_lock` (app.py:51) only serializes two
`/api/handoff` calls; it does not protect the other endpoints. This is the exact
designed workflow (click handoff, keep reviewing while it encodes), so it is
reachable by a single user. **No corruption** — the transaction rolls back and
slices stay `selected`; reads (`GET /api/slices`) are unaffected.

**Regression to write first (fail-before-fix):** two connections to one temp DB;
open an `immediate_transaction` on the first and drive a slow fake extractor
(e.g. `time.sleep`); assert that a `update_slice_status` on the second currently
raises `OperationalError` — then that after the fix it succeeds (or returns a
clean 409/503).

**Repair options:**
1. **(Recommended) Encode outside the write lock; keep the lock for a short
   critical section.** Extract all clips to the batch dir first, then open
   `immediate_transaction` only to re-read the `selected` set, confirm it is
   unchanged, write the manifest, and `bulk_update_status`. Preserves the
   anti-double-delivery guarantee (the lock still spans the re-read → mark) while
   dropping the multi-second hold. Most faithful to the original intent; largest
   change — the re-read must reconcile if selection changed during encode.
2. **WAL + a bounded busy timeout.** `PRAGMA journal_mode=WAL` and
   `sqlite3.connect(..., timeout=N)` / `PRAGMA busy_timeout`. WAL lets readers
   proceed and a larger timeout lets a *short* handoff finish before a writer
   gives up — but a long batch still blocks writers for its full duration, so
   this narrows the window rather than closing it. Cheapest; partial.
3. **Graceful degradation only.** Catch `OperationalError` in the write endpoints
   and return 409/503 "a handoff is in progress, retry shortly." Removes the
   opaque 500 but the review action still fails during a handoff. Good as a
   companion to (1) or (2), weak alone.

Recommendation: (1) as the real fix, with (3) as a cheap safety net. (2) alone
does not resolve the finding.

---

## B1 — Clip can be shorter than the selected window; manifest misreports it as exact *(High)*

**Where:** `ricesearcher/handoff/extract.py:76-81`, `ricesearcher/handoff/writer.py:143,152-164`.

**What:** after the ffmpeg trim, `extract()` probes the output **only** for
`math.isfinite(output_duration) and output_duration > 0` (extract.py:80-81) and
then **discards** the probed value — it never compares it to the requested
`duration = end - start`. `_manifest_clip` (writer.py:143) then sets
`clip.duration = target_out - target_in` (the *requested* length) and stamps
`source_window` with the requested `target_*`.

**Trigger → effect (verified):** `pipeline.py:196` sets `target_out = window.end`
from ASR word timestamps and clamps only `pad_out` to source duration, never
`target_out`. `ffprobe_duration` reads the container `format=duration`, which for
remuxed / yt-dlp sources can exceed the last actually-decodable frame. Both the
`PATCH` bound (app.py:290) and the handoff pre-flight (writer.py:250) accept
`target_out` up to that estimate. A reviewer selecting a window that ends at or
near the source end can therefore have `ffmpeg -t` run out of frames and exit 0
with a **shorter** file; the existence-only probe passes, and the manifest
declares the requested (longer) duration. This directly contradicts SPEC §7's
unconditional promise that "the clip file is exactly the reviewed target
interval," and it undercuts PR33's stated fail-closed intent — the code fails
closed on an *unmeasurable* duration, not a *wrong* one. Bounded to
near-boundary truncation (not an inverted or unbounded window), and Clipper
independently re-transcribes the bytes, which limits blast radius — hence High,
not Critical.

**Regression to write first:** a fake extractor that writes an output whose
probed duration is materially shorter than requested; assert the current code
still reports the requested duration in the manifest — then that the fix either
rejects the clip or records the measured duration.

**Repair options:**
1. **(Recommended) Measure and enforce.** Have `extract()` return the probed
   `output_duration`; in `write_batch`/`_manifest_clip` reject (raise
   `HandoffError`) when it is short of requested beyond a small frame-time
   tolerance. Strongest guarantee; a genuinely near-boundary clip may now fail
   and need the reviewer to nudge `target_out` in.
2. **Measure and record.** Return the probed duration and write the *measured*
   value into `clip.duration`/`source_window` instead of the requested one, so
   the manifest never overstates. Keeps every clip flowing; downstream sees the
   truth. Weaker on the "exact" promise but honest.
3. **Doc-only fallback.** If neither is wanted now, soften SPEC §7's
   unconditional wording to state the near-boundary caveat. Lowest effort;
   leaves the silent mismatch in place — least preferred.

Recommendation: (1) if "exact" is a hard contract; (2) if throughput matters
more than rejecting boundary clips. Either removes the silent misreport.

---

## B2 — One over-duration slice rejects the whole batch *(Medium)*

**Where:** `ricesearcher/handoff/writer.py:250-254`, `ricesearcher/pipeline.py:196`.

**What / trigger → effect (verified):** because `target_out` is never bounded to
source duration at scoring (pipeline.py:196 — only `set_window`, an edit the
reviewer may never make, enforces it), a legitimately-scored, never-edited slice
can have `target_out` slightly greater than the probed container duration when
Whisper emits a final word end past true media end. writer.py:250 then raises
`HandoffError`, which `do_handoff` maps to a **409** — so the reviewer selects
several good slices, clicks handoff, and gets a single "clip N: target_out
exceeds source duration" with **zero** clips delivered, including the valid ones.
Recovery requires guessing which slice and shrinking its window by hand. Unlike A
this is a graceful, correct-state error (nothing is marked), so it is Medium.

**Repair options:**
1. **(Recommended) Clamp instead of reject at the handoff boundary.** When
   `target_out` exceeds the probed duration by a small tolerance, clamp it to the
   duration for that clip (ffmpeg's `-t` already stops at EOF) rather than
   failing the batch. Resolves B2 and dovetails with B1 option 1's tolerance.
2. **Per-clip skip with a report.** Skip only the offending clip(s), hand off the
   rest, and return which were skipped. Preserves strictness but no longer lets
   one slice poison the batch. Larger API/response change.
3. **Bound `target_out` at scoring.** Clamp `target_out` to
   `max(duration_s, last_word_end)` in `pipeline.py` alongside `pad_out`.
   Prevents the state at the source, but `duration_s` at scoring may differ from
   the probe at handoff, so keep a handoff-side guard too.
4. **Defer.** File as a linked follow-up Issue if handoff-time batch failures are
   rare in practice; acceptance criteria = "a single over-duration slice never
   blocks the delivery of valid selected slices."

Recommendation: (1)+(3) together (bound at scoring, clamp defensively at
handoff) if fixing now; otherwise (4).

---

## C — `extract()` `RuntimeError` bypasses `do_handoff`'s error mapping → 500 not 503 *(Low–Medium)*

**Where:** `ricesearcher/handoff/extract.py:79,81`, `ricesearcher/web/app.py:318-327`.

**What / trigger → effect (verified):** `extract()` raises a **bare**
`RuntimeError` ("ffmpeg output could not be probed" / "has no usable duration").
`do_handoff` catches `HandoffError`, `OSError`, `subprocess.SubprocessError`
only, so a `RuntimeError` (e.g. ffmpeg exits 0 but disk filled mid-encode and the
output is unprobeable) propagates uncaught → raw 500, instead of the graceful 503
"selected slices remain selected for retry" this PR was built to guarantee. DB
state stays correct/retryable (transaction rolls back), so the practical harm is
a misleading 500 and stack trace on a narrow error path. Note: A's
`OperationalError` is uncaught by this same narrow `except` list — both point at
`do_handoff`'s error mapping.

**Repair options:**
1. **(Recommended) Raise the domain error.** Make `extract()` raise
   `HandoffError` (or a new `ClipExtractError(HandoffError)`) instead of bare
   `RuntimeError`; `do_handoff` already maps `HandoffError` → 409 (consider 503
   for execution failures). Smallest, most precise.
2. **Broaden the `except`.** Add `RuntimeError` (and `sqlite3.OperationalError`
   for A's benefit) to the `do_handoff` handler with a 503 mapping. Fixes C and
   A's symptom in one place; slightly blunter.

Recommendation: (1) for extraction failures; if you take A option 3, fold the
`OperationalError` catch in there.

---

## D — Dead `HandoffEntry.pad_in/pad_out` fields *(Low, cleanup/defer)*

`HandoffEntry.pad_in/pad_out` (writer.py:44-45) are populated from
`slice_.pad_in/pad_out` (writer.py:186-187) but no longer read anywhere:
`_manifest_clip` now sources the manifest's `pad_*` from `target_*`
(writer.py:152-157, intentional per the ADR Q4 amendment) and the validity check
no longer references them. They are write-only. Not a live defect, but a future
maintainer editing upstream `pad_*` would reasonably expect handoff output to
change, and it silently would not. **Disposition:** remove the two fields (and
their assignment) or add a comment that they are retained only as review context;
defer to a cleanup Issue if not touching this file now.

## E — `PROFILE_ID_PATTERN` lost its anchors *(Low, cleanup/defer)*

`beat/profile.py:24` changed to `re.compile(r"[a-z0-9][a-z0-9-]{0,39}")` (no
`^…$`). Correctness now depends on every caller using `.fullmatch()`. All current
call sites do (profile.py:60,118; web/app.py:117), independently confirmed by R1
— **no live defect**. But an exported module-level pattern that is unsafe with
`.match()`/`.search()` is a latent trap: a future `.match()` caller would accept
an id with a trailing invalid suffix that flows into a filesystem path join
(profile.py:106) and a SQL parameter. **Disposition:** re-add `^…$` (or `\Z`)
anchors so the pattern is safe-by-default regardless of call style; defer to a
cleanup Issue otherwise. (Cheap and low-risk to just re-anchor.)

## Housekeeping note (not a formal finding)

R1 flagged, at low confidence and did not report, that
`Library.upsert_slices` (store.py:~397-400) may have no production callers after
PR27 (likely test-only). Worth a grep if you touch `store.py`; out of scope here.

---

## Surfaces independently inspected and found clean

Recorded so these are not re-litigated:

- **Schema migration** (store.py:133-164): per-statement split via
  `sqlite3.complete_statement` under one `BEGIN IMMEDIATE`; committed meta table
  first; concurrent opener re-reads version and skips — no double-apply / partial
  migration. (R1, R2, R4, PG concur.)
- **PR29 path equivalence** (store.py:244-257, `_canonical_media_path`;
  app.py:361-383): both sides `str(Path(...))`-canonicalized so `./`, redundant
  separators, and legacy rows all match; the source row is deleted before the
  shared-reference scan, so a sibling reference blocks the unlink (no orphan, no
  unlink of shared media); no over-match. Symlink/case/`..` explicitly out of
  scope and unreachable via the content-addressed cache's own path generation.
  (R1, R2 concur.)
- **Lifecycle / terminal-state preservation** (store.py `replace_candidate_slices`,
  `update_duplicate_annotations`, `AND status != 'handed_off'` guards; app.py
  409/404 re-checks): human-touched rows protected across re-score/dedup; dup
  annotation is a scoped `dup_*` UPDATE that no longer reverts status. (R1, R2 concur.)
- **Manifest-last atomicity** (writer.py:120-137): manifest via `os.replace`
  last; `finally` (not `except`) `rmtree` so Ctrl-C mid-encode leaves no
  manifest-less batch. (R1, R2 concur.)
- **Transcript-window rebuild** (writer.py:174-179): half-open overlap
  `word.start < target_out and word.end > target_in` — correct, no off-by-one.
  (R1, R2, R3 concur.)
- **`set_window` bounds** (app.py:261-294): NaN/Inf, negative, empty, inverted,
  and out-of-source all correctly rejected per FR-8. (R3 concur.)
- **JS review UI** (app.js): stale-request guard (`isCurrentLoad`), per-card
  mutation-pending state, and `fmt()` exponent expansion inspected clean for
  realistic ranges. (R1, R3 concur.)
- **Docs** (SPEC, ADR-001 Q4, README, riceclipper-pickup-plan.md): internally
  consistent; the only divergence is substantive, not textual — SPEC §7's
  unconditional "exact" claim vs. what B1 shows the code guarantees.

---

## Suggested next steps (maintainer)

1. **Fix together:** A (option 1 + 3) and B1/B2 (B1 option 1 or 2 + B2 option 1/3)
   — they share the handoff path and the `target_out`/duration root cause. Fold C
   option 1 in while touching `do_handoff`/`extract()`.
2. **Defer with linked Issues:** D and E (low-risk cleanups), plus the
   `upsert_slices` grep.
3. Each accepted repair lands with a fail-before-fix regression per the project's
   verification discipline. This session applied none.
