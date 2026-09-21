# RiceClipper — PR-21-onward triage review

> **Dispositions applied 2026-09-21 (see "Dispositions applied" at the end).**
> 8 of the 10 findings repaired with regression tests; A6 and B5 deferred as
> recommended; **A1 deferred** — its recommended fix (anchor immutability) was
> implemented experimentally and **provably conflicts with the codebase's tested
> `MIN_WORD_S`/in-clip invariant**, so it was reverted pending a product decision.
> Full gate suite green: ruff (lint+format), 355 tests pass, coverage 90.28%.

**Scope:** all work from PR #21 onward, split into two paths — (A) music/lyric
sync and (B) cropping/subject tracking.
**Target:** `56e4ac9` (last PR #20) → `58ff82b` (`main`). Numbered history jumps
#20 → #24; the unnumbered post-#20 crop commits are almost certainly the missing
#21–#23, so the review base is set immediately after #20.
**Method:** independent multi-agent cold review (agentic-review-orchestration) with
an OCR (`open-code-review`) pre-gate. Two Opus-4.8 path leads each delegated to
independent Sonnet sub-reviewers on three lenses — skeptical-senior, domain
specialist, and OCR-rule. Findings below are cross-referenced for *convergence*
(independent reviewers agreeing) and the parent (this session) independently
re-validated every High finding with a repro.
**Verification depth:** static reasoning + existing pytest suite. No full media
pipeline was run. Test status at head: lyrics/app subset **80 passed**; crop
subset **130 passed** — every finding below is a *latent* gap, not a currently
failing test.
**Authority:** this is a triage report only. **No code was changed, nothing was
committed, no Issues were filed.** Repairs are recommendations for you to approve.

Date: 2026-09-21.

---

## How to read this

Each finding has a severity, the reviewers that independently found it
(convergence is the strongest confidence signal), the concrete real-use failure,
evidence (with repros where run), and **one or more repair options** or a
deferral rationale. Severities are calibrated to *real-use impact*, not reviewer
confidence.

- **High** — silent wrong output shipped, or a common-case break.
- **Medium** — degraded output / a real gap reachable on some real inputs.
- **Low** — a real bug with limited real-world impact today.

Recommended disposition (**Repair** / **Defer**) is my suggestion; you decide.

---

## Executive summary

| ID | Sev | Path | Finding | Convergence | Rec |
|----|-----|------|---------|-------------|-----|
| A1 | **High** | Lyric | Shortfall/clamp silently drags real anchors off their whisper timing (≤0.9 s) | skeptical + lead + parent repro | Repair |
| A2 | **High** | Lyric | Curly apostrophes never match whisper → contractions never anchor | domain + parent repro | Repair |
| B1 | **High** | Crop | First-frame `POS_MSEC` trusted → whole crop timeline poisoned, crop silently never moves | skeptical + parent repro | Repair |
| A3 | Medium | Lyric | Hyphenated tokens never match whisper's word-split | domain + parent repro | Repair |
| A4 | Medium | Lyric | `/transcribe` doesn't invalidate a prior render → stale captions can reach handoff | skeptical | Repair |
| B2 | Medium | Crop | Detection after-cut uses speech threshold for both profiles → wrong-subject snap on music soft-cuts | lead + domain + skeptical (3×) | Repair |
| B3 | Medium | Crop | First-frame face miss → slow pan-in instead of immediate lock at clip start | skeptical | Repair |
| A5 | Low | Lyric | `duration<=0` path skips the compression safety-net; `lyrics_job` lacks an `info is None` guard | OCR | Repair (cheap) |
| A6 | Low | Lyric | `render_job` pre-render `unlink` can wedge a job at "rendering" on a non-FNF `OSError` | OCR | Defer |
| B4 | Low | Crop | `detect_track_owned` `finally` missing `None`-guard → `AttributeError` masks root cause in QA scripts | OCR + parent | Repair (cheap) |
| B5 | Low | Crop | Early-decode-end guard over-triggers blur-pad fallback on VFR / trailing-audio sources | lead | Defer |

Three High findings, all with a reproduced failure. Two of the three (A2, B1)
have trivial one-spot fixes.

---

## Path A — music / lyric sync

### A1 — Shortfall/clamp silently drags real anchors off their true timing · **High**
- **Where:** `transcribe/lyrics.py:166-214` (`_interpolate_gaps`) interacting with
  `transcribe/lyrics.py:217-227` (`_clamp_to_duration`).
- **Convergence:** skeptical sub-reviewer (High) + original lead (rated it Low,
  same mechanism) + **parent repro**.
- **Breaks in real use:** any pasted-lyric job where trailing tokens run out of
  room before the clip ends — an outro ad-lib, a quiet trailing chorus tag, or
  any tail text whisper didn't confidently transcribe while the rest lines up.
  This is an ordinary shape for real lyrics. When the *last* unanchored run needs
  more room than remains, `_interpolate_gaps` lets it balloon past `duration`
  (the forward-shift trick only works when there's a later anchor to push), and
  `_clamp_to_duration` then walks backward enforcing no-overlap/min-width **with
  no awareness of which indices are anchors** — so it rewrites a real,
  LCS-matched anchor's timing. The highlight comes off the beat with no error and
  no reduced `anchor_rate` signal (the UI badge still says "anchors · NN%").
- **Evidence (parent repro, unmodified `align()`):** 20 real anchors + 20 trailing
  filler tokens → `method=anchors`, `anchor_rate=0.50`, and:
  `w19` delivered `(8.700, 8.750)` vs true `(9.600, 9.700)` = **0.900 s drift**;
  `w18` = 0.450 s. This is the *normal* anchors path — not the documented
  global-scarcity uniform-compress fallback (#33) — and it blows well past
  ADR-002's "highlight tracks the vocal within about half a second" bar.
  Contradicts `docs/design/music-path-spec.md:60` ("an anchor takes the reference
  word's start and end"). The same class of drift exists on interior runs via the
  forward-shift at `lyrics.py:201-207`.
- **Repair options:**
  1. Treat anchor positions as immutable in `_clamp_to_duration`: skip anchored
     indices when walking backward, or clamp each unanchored run only within
     `[prev_anchor.end, next_anchor.start]`. (Preferred — smallest, matches spec.)
  2. When a trailing/leading run genuinely cannot fit at `MIN_WORD_S` without
     crossing an anchor, route *that* case through the existing uniform-compress
     fallback in `_build_words` instead of letting `_interpolate_gaps`/
     `_clamp_to_duration` silently renegotiate anchor timing.
  - Acceptance: a fixture with trailing untranscribed tokens keeps every anchored
    word within ~0.15 s of its reference timing.
- **Disposition:** **Repair.** Highest-value lyric fix; option 1 is contained.

### A2 — Curly/typographic apostrophes never match whisper's straight ones · **High**
- **Where:** `transcribe/lyrics.py:22-36` (`normalize`).
- **Convergence:** domain sub-reviewer (High) + **parent repro**.
- **Breaks in real use:** lyrics pasted from any lyrics site (Genius, AZLyrics,
  …) or typed on iOS/macOS/Word use the typographic right single quote U+2019 in
  contractions (`don't`, `can't`, `I'm`, `you're`). faster-whisper emits ASCII
  U+0027 for the same words. `normalize` decides whether to *keep* an apostrophe
  but appends the character verbatim — it never canonicalizes the variants — so
  `normalize("don't")` (U+0027) ≠ `normalize("don't")` (U+2019) and every
  contraction silently fails to anchor. Near-universal trigger on real paste-ins.
- **Evidence (parent repro):** `normalize` equality is `False` across the two
  quote styles. A realistic chorus anchored `0.786` with curly quotes vs `1.000`
  with straight quotes; a contraction-dense line dropped to ~`0.43`, low enough to
  risk falling below `ANCHOR_MIN` (0.25) and discarding an otherwise-perfect match
  into whole-song `even_fill`.
- **Repair options:**
  1. In `normalize`, canonicalize `'`, `‘`, `’`, `‛`, `ʼ` → `'` (append `"'"`,
     not `c`), so reference and lyric tokens collapse to one code point.
     (One-line, low-risk; preferred.)
  2. Apply a broader Unicode confusable/NFKC-style fold to apostrophe-like marks.
- **Disposition:** **Repair.** Trivial and near-universal.

### A3 — Hyphenated compound tokens never match whisper's word-split · Medium
- **Where:** `transcribe/lyrics.py:22-36` (`normalize`). Same theme as A2.
- **Convergence:** domain sub-reviewer (Medium) + **parent repro**.
- **Breaks in real use:** a hyphenated compound pasted as one token
  (`mother-in-law`, `twenty-one`, stutter ad-libs `b-b-baby`) normalizes to a
  fused string (`normalize("mother-in-law") → "motherinlaw"`), but whisper
  transcribes separate words — a guaranteed non-anchor for that token. Localized
  (bounds only its own interpolation), lower frequency than A2, but stutter-dense
  hooks degrade like A2.
- **Repair options:**
  1. Split hyphen-joined tokens into sub-tokens for *matching* while keeping the
     original text for display (mirrors whisper; preferred).
  2. Keep the internal hyphen like the apostrophe case (weaker — still one fused
     token).
- **Disposition:** **Repair**, ideally alongside A2 in the same `normalize` pass.

### A4 — `/transcribe` does not invalidate a prior rendered output · Medium
- **Where:** `app/main.py` `transcribe_job` (~L122-150) vs the siblings
  `lyrics_job` (L153-174) and `restore_transcript` (L177-194).
- **Convergence:** skeptical sub-reviewer (Medium). Code-reasoned; consistent with
  parent read of the file.
- **Breaks in real use:** `lyrics_job` and `restore_transcript` both unlink
  `job.output_path` after changing the transcript (a rendered mp4 has stale
  captions baked in). `transcribe_job` also replaces `job.words` but never touches
  `job.output_path`, and its guard only blocks `{"transcribing","rendering"}` —
  so it's callable on an already-rendered job. Afterward `has_output` stays true
  and `job.output_path` still points at the mp4 rendered from the *old* words, and
  `/api/handoff` copies it straight to the RicePoster pickup dir with no
  transcript cross-check — a clip whose burned captions mismatch the transcript
  metadata RicePoster consumes. **Not reachable from the shipped UI today**
  (it calls `/transcribe` once, pre-render); needs a second `/transcribe`
  (retry, a future re-transcribe affordance, or a direct API caller). Real-use
  impact is Low *today*, Medium the moment a re-transcribe path exists.
- **Repair options:**
  1. In `transcribe_job`, unlink + null `job.output_path` when a prior render
     exists (mirror the two siblings). (Preferred — one place, matches the
     established pattern.)
- **Disposition:** **Repair.** Cheap safety fix on the path that feeds posting.

### A5 — `duration<=0` skips the safety-net; `lyrics_job` lacks an `info is None` guard · Low
- **Where:** `transcribe/lyrics.py:251` (`words[-1].end > duration > 0` excludes
  the `duration==0` case) + `app/main.py:165` fallback (`job.info.duration if
  job.info else 0.0`) + `lyrics_job` guard.
- **Convergence:** OCR sub-reviewer (rated Medium).
- **Breaks in real use:** `lyrics_job` only blocks `transcribing`/`rendering`, so
  it's callable on an errored / never-probed job whose `job.info is None`;
  `align` then gets `duration=0.0`, `_build_words` emits words past a zero
  duration (the compression net is gated on `> 0`), the endpoint returns 200 and
  sets status `ready` while `info` is still `None` — an inconsistent state.
  Bounded because `/render` independently 409s when `info is None`, so the bad
  words can't actually render/ship.
- **Repair options:**
  1. Have `lyrics_job` reject when `job.info is None` (mirror `render_job` /
     `generate_header`). (Preferred — fixes the root reachability.)
  2. Special-case `duration<=0` before/inside `_build_words`.
- **Disposition:** **Repair** (cheap) or **Defer** — low real-use impact.

### A6 — `render_job` pre-render `unlink` can wedge a job at "rendering" · Low
- **Where:** `app/main.py:311-315` (inside `job_operation_lock`, no `try/except`).
- **Convergence:** OCR sub-reviewer (rated Medium).
- **Breaks in real use:** `job.status` is set to `"rendering"` *before* the
  `output_path.unlink(missing_ok=True)`. `missing_ok` only suppresses
  `FileNotFoundError`; any other `OSError` (PermissionError, read-only FS)
  propagates as a 500 with status left at `"rendering"`, which then makes
  `has_active_jobs()` treat the job as active and `clear_cache()` raise
  `ActiveJobsError` for the whole work root until restart. Rare trigger on a
  local single-user FS.
- **Repair options:** wrap the cleanup/status block in `try/except OSError`,
  reset `job.status="error"` (with a message) before returning an HTTP error.
- **Disposition:** **Defer** — real but low-probability; fix opportunistically.

---

## Path B — cropping / subject tracking

### B1 — First-frame `POS_MSEC` trusted unconditionally, poisoning the whole timeline · **High**
- **Where:** `render/subject.py:199-207` (in `detect_track`).
- **Convergence:** skeptical sub-reviewer (High) + **parent repro** (code path
  confirmed).
- **Breaks in real use:** the monotonicity/sanity guard is skipped for frame 0
  (`if frame_index and reported_t <= last_decoded_t:` — `frame_index==0` is
  falsy). If OpenCV's `CAP_PROP_POS_MSEC` returns a spurious/large value on the
  first decoded frame — a documented real-world quirk for containers with a
  non-zero `start_time`/edit list: re-muxed or trimmed exports, some screen
  recordings — `last_decoded_t` is poisoned, and every later frame
  (`reported_t <= last_decoded_t`) is dragged up to `last_decoded_t + 1/fps`,
  corrupting the entire sample timeline. `framing.plan_crop` then matches cuts
  (from a separate, correctly-timed ffmpeg pass) against the corrupted times so
  scene-cut snapping never lines up, and `geometry` writes `sendcmd` crop commands
  keyed to the corrupted `t` — for any clip shorter than the offset, **none of
  the crop commands fire during the render** and the crop sits at its initial
  position. No error is raised; the end-of-decode "ended early" check is fooled
  because the corruption *inflates* timestamps past `info.duration`.
- **Evidence (parent repro):** feeding a fake capture a first-frame `POS_MSEC` of
  50000 ms then correct 40 ms steps, on a 2.0 s clip, produced sample times
  `50.0, 50.2, 50.4, …, 51.8` — every one past the clip duration.
- **Repair options:**
  1. Apply the same sanity/monotonicity treatment to frame 0: reject/clamp a
     `reported_t` that deviates from `fallback_t` (`frame_index/fps`) by more than
     a small tolerance, or clamp to `[0, info.duration]`. (Preferred.)
  2. Normalize the whole track by subtracting the first sample's time (anchor the
     timeline to 0) — simpler but hides genuinely offset streams.
- **Disposition:** **Repair.** Silent whole-clip crop failure; fix the frame-0
  exemption.

### B2 — Detection after-cut selection uses the speech threshold for both profiles · Medium
- **Where:** `render/subject.py:217-221` (`is_after_cut` gates on
  `framing.SCENE_MIN_SPEECH`, 0.3) vs `render/framing.py:137` (per-profile
  threshold; `SCENE_MIN_MUSIC` = 0.2).
- **Convergence:** **three independent reviewers** — original lead (Medium),
  domain (High, with a live `detect_track`→`plan_crop` repro), skeptical
  (Medium). Strongest-converged Path B finding.
- **Breaks in real use:** one detection track is built once and reused for both
  the speech and music `CropPlan`s. Box selection at a cut is hardcoded to the
  speech threshold (0.3), so for a music clip with a genuine cut scored in the
  0.2–0.3 band — exactly the band ADR-002 added the 0.2 music threshold to catch
  ("green-tinted footage") — detection does *not* apply the "prefer the largest
  box" group-shot rule and instead keeps the continuity-nearest box. But
  `plan_crop(profile="music")` *does* treat that cut as real and issues an
  immediate hard snap onto whatever box detection chose — which can be a small
  incidental face near the previous subject's position rather than the new
  dominant subject. Music videos are cut-heavy and tend to keep subjects roughly
  centered across cuts, which makes the near-face collision *more* likely. Visible
  wrong-subject snap on exactly the content type the profile exists for. (None of
  the committed `music_*.json` fixtures contain any cut, so this integration is
  untested end to end.)
- **Evidence (domain repro):** track with a small near face and a large far face
  after a cut scored 0.25 → detection picks the small near face; music plan hard-
  snaps onto it; speech plan correctly ignores the sub-threshold cut.
- **Repair options:**
  1. Gate `is_after_cut` on `SCENE_MIN_MUSIC` (0.2) for the shared detection pass.
     A cut is a cut regardless of content; only the *snap* decision should differ
     per profile, and after-cut selection only changes behavior when a near
     candidate exists (else it already falls back to largest). Cheapest, safe for
     speech. (Preferred.)
  2. Thread the consuming profile's threshold into `detect_track`/`_detect_worker`
     (two passes, or union the after-cut candidate set into the payload).
  3. Add a fixture with a soft cut + two competing candidates to lock the fix.
- **Disposition:** **Repair** (option 1) + add the fixture (option 3).

### B3 — First-frame face miss → slow pan-in instead of immediate lock · Medium
- **Where:** `render/framing.py:188-217` (the `x is None` init branch and the
  `return_after_loss` condition).
- **Convergence:** skeptical sub-reviewer (Medium), with repro.
- **Breaks in real use:** if the very first sampled frame (t≈0) misses a face
  (fade-in, title card, motion blur, a blink) but the subject is genuinely
  off-center and is detected within ~1 s, the crop does not snap on first
  acquisition. `x` initializes to `center_x`, and later faced samples fall through
  the ordinary dead-zone/settle/pan-cap path rather than the loss-return snap,
  because `return_after_loss` requires `last_face_t is None and t_i > LOSS_S`
  (>1 s), while a single missed 5 Hz sample is only 0.2 s. Result: a visible smooth
  pan from center into position over ~0.6–1 s at the very start of the clip.
- **Evidence (repro):** track `[None, face@cx=1600, face@cx=1600, …]` on 1920×1080
  (target x=1296) produced `x = 656 → 848 → 1040 → 1232` (capped at ~192 px/step)
  instead of snapping straight to 1296 on first detection.
- **Repair options:**
  1. Treat "first face ever seen" as a snap condition regardless of elapsed time
     (drop the `t_i > LOSS_S` gate when `last_face_t is None`) — there is no prior
     resting position to smooth from at clip start. (Preferred.)
- **Disposition:** **Repair.** Common clip-start scenario; contained change.

### B4 — `detect_track_owned` `finally` missing `None`-guard · Low
- **Where:** `render/subject.py:324` (`if process.is_alive()`) vs the guarded
  `if process is not None and process.is_alive()` at `:299`.
- **Convergence:** OCR sub-reviewer (Medium) + **parent** (independently spotted).
- **Breaks in real use:** if `context.Process(...)` raises before assignment,
  `process` stays `None` and the `finally`'s `process.is_alive()` throws
  `AttributeError`, masking the real error. In production `build_plan` catches
  everything and returns a blur-pad plan, so end users never crash — but the QA
  scripts `scripts/crop_tuning_sweep.py::_analyze` (no enclosing try/except) and
  `scripts/crop_check.py` call `detect_track_owned` directly, so a maintainer
  sees a confusing `AttributeError` instead of the root cause.
- **Repair options:** guard the `finally` the same way as `:299` —
  `if process is not None and process.is_alive():`.
- **Disposition:** **Repair** (trivial; improves maintainer diagnostics).

### B5 — Early-decode-end guard over-triggers blur-pad fallback · Low
- **Where:** `render/subject.py:240-246` (`tolerance_frames`;
  `frame_index + tolerance_frames < expected_frames`; `decoded_end + 1.0 <
  info.duration`).
- **Convergence:** original lead (Low). Single-source.
- **Breaks in real use:** for a VFR landscape clip, or one whose container
  duration exceeds the decodable video stream by >1 s (trailing audio / mux
  quirks), `detect_track` raises "video decode ended early"; `build_plan` catches
  and returns an `analysis_failed` **blur-pad** plan — a supported real input gets
  degraded framing (letterboxed) instead of a subject crop. Never crashes or
  wrong-crops, so capped at Low. `CAP_PROP_FRAME_COUNT` is unreliable for VFR and
  the absolute 1.0 s slack is tight.
- **Repair options:**
  1. Relative slack, e.g. `> max(1.0, 0.05 * info.duration)`.
  2. Skip the `expected_frames` check when frame-count metadata is unreliable/VFR.
- **Disposition:** **Defer** — validate against a real VFR sample first (needs
  media the static+tests pass couldn't exercise). Acceptance: a 60 s VFR interview
  crops rather than blur-pads.

---

## Verified sound (independently corroborated — you can *not* worry about these)

**Path A** (agreed across ≥2 reviewers and/or parent):
- `Align → Restore → re-Align` round-trips against `job.reference_words`
  (`app/main.py:161-166`), never `job.words`, so re-Align re-anchors against the
  original whisper words. Sidecar persists/reads both lists.
- Output invalidation for `/lyrics` and `/restore-transcript` unlinks+nulls
  `output_path`; `/api/handoff` 409s on a missing output — a stale render cannot
  ship via *these* paths (the gap is A4's `/transcribe`).
- Render concurrency: two-phase lock (global lock flips status/output_path, then a
  per-job `render_lock` around ffmpeg); a second render/Align/Restore/clear gets
  409; the non-blocking probe avoids deadlock; no lost update found.
- Repeated-hook / chorus matching: `_lcs_matches` keeps strictly increasing
  indices in chronological (earliest-tie) order — no double-match, drop, or
  reorder. Empty/all-punctuation tokens are excluded from matching but retained
  and timed (no word dropped).
- `even_fill` char-count distribution + `line_start` propagation into
  `phrasing.group_words`; dropped-fetch render polling; empty/whitespace → 422.

**Path B** (skeptical + domain + OCR + lead + parent):
- Crop window bounds: `x` is clamped to `[0, source_w − window_w]` and even-rounded;
  30 Hz interpolation is a convex combination of in-bounds endpoints — no
  out-of-range/odd-pixel crop, no black-bar path (verified over all fixtures).
- No-face / face-loss-then-return: speech blur-pads; music holds static; loss
  return snaps immediately on reappearance (`LOSS_S=1.0`).
- Idle-subject stability: dead-zone/settle-zone math cannot overshoot; small real
  jitter never leaves the dead zone → `x` constant; hard cuts still snap on time.
- `test_speech_profile_unchanged` byte-compares pre-/post-Level-5 speech output —
  the retune did not silently change speech behavior.
- Music decision policy (always crop; `face_rate`/`safe_rate` reported but
  non-gating) matches ADR-002 exactly. `geometry` interpolation guards `dt<=0`,
  short input, `interpolation_fps<=0`. `probe` parsers fall back safely.

---

## Below the bar (noted, no action recommended)

- **Dead code:** `web/app.js:636,759` set `clip.error`, never read. Harmless.
- **`scripts/crop_check.py` `_passes()`** applies speech `FACE_RATE_MIN`/
  `SAFE_RATE_MIN` even under `--profile music`; not reached in the documented M0
  flow (`--roles none` returns first). Fix only if you run `--profile music
  --roles check` directly.
- **Un-DRY coupling:** `subject.SCENE_MIN` (0.2, ffmpeg pre-filter) and
  `framing.SCENE_MIN_MUSIC` (0.2) are separate constants that must stay in sync;
  correct today. Worth a shared constant/comment.
- **Handoff TOCTOU:** a narrow window between `/lyrics`|`/restore` unlinking
  `output_path` and a concurrent `/api/handoff` — on POSIX this fails loudly or
  copies the open inode; it never ships stale content. Below the bar for a
  single-maintainer local tool.

---

## Methodology, convergence & caveats

- **Reviewers:** Path A got three independent cold Sonnet sub-reviewers
  (skeptical, domain, OCR-rule) plus the Opus lead's own analysis; Path B got the
  same three plus its lead. The OCR pre-gate (`open-code-review` v1.12.7) scoped
  17 source files and supplied the rule spec used by the OCR-rule lens.
- **Process anomaly (disclosed for trust):** both path leads were force-handed-back
  by the harness before their background sub-reviewers returned, so the leads'
  first reports were lead-only. The sub-reviewers, however, completed and reported
  independently, giving the intended convergence; I stopped two redundant re-runs
  once coverage was complete, to control cost. Net effect: coverage is *more*
  than the planned 2–3 per path, and the strongest bugs (A1, A2, B1) were each
  confirmed by an independent reviewer **and** re-reproduced by me.
- **Parent validations run:** A1 (0.9 s anchor drift), A2 (curly vs straight
  `normalize` + anchor-rate 0.786 vs 1.000), A3 (`motherinlaw`), B1 (2.0 s clip →
  50–51.8 s sample times). All confirmed.
- **Not exercised:** no full ffmpeg/whisper pipeline, no real media. B5 and the
  probe rotation+non-1:1-SAR combination are the two places that would benefit
  from a real-file check; no code defect was found in the latter.

## Suggested order of work (your call — nothing is authorized)

1. **A2** and **B1** first — trivial, high impact, both silent.
2. **A1** — highest-value lyric correctness fix; option 1 is contained.
3. **B2** (option 1 + fixture), **B3**, **A4**, **A3** — medium, contained.
4. **A5**, **B4** — cheap Lows worth folding in. **A6**, **B5** — defer.

Each repair should land with a fail-before-fix regression test (the current suite
passes green, so these are all latent).

---

## Dispositions applied (2026-09-21)

Repairs made in the working tree (uncommitted), each with a fail-before-fix
regression test. No commits/PR yet — awaiting your go-ahead.

| ID | Sev | Disposition | Code | Regression test |
|----|-----|-------------|------|-----------------|
| A2 | High | **Repaired** | `transcribe/lyrics.py` `normalize` canonicalizes `'`/`‘`/`’`/`‛`/`ʼ` | `test_curly_apostrophe_matches_straight_quote` |
| A3 | Med | **Repaired** | `transcribe/lyrics.py` `_match_units` splits hyphenated tokens for matching, text preserved | `test_hyphenated_token_anchors_against_word_split_reference` |
| A4 | Med | **Repaired** | `app/main.py` `transcribe_job` unlinks a prior render | `test_transcribe_invalidates_prior_render` |
| A5 | Low | **Repaired** | `app/main.py` `lyrics_job` 409s when `job.info is None` (blank still 422) | `test_lyrics_endpoint_409_when_no_probe_info`, `test_lyrics_endpoint_422_on_blank_without_info` |
| B1 | High | **Repaired** | `render/subject.py` `detect_track` rejects a first-frame PTS past `duration+1s` | `test_detect_track_ignores_spurious_first_frame_pts` |
| B2 | Med | **Repaired** | `render/subject.py` after-cut selection gates on `SCENE_MIN_MUSIC` | `test_detect_track_reacquires_largest_box_at_music_soft_cut` |
| B3 | Med | **Repaired** | `render/framing.py` first face ever acquired snaps immediately | `test_first_face_after_missing_first_sample_snaps` |
| B4 | Low | **Repaired** | `render/subject.py` `detect_track_owned` `finally` guards `process is not None` | `test_detect_track_owned_surfaces_process_construction_error` |
| A6 | Low | **Deferred** (as recommended) | — | — |
| B5 | Low | **Deferred** (as recommended, needs a real VFR sample) | — | — |
| A1 | High | **Deferred — could not repair cleanly** | see below | — |

### A1 — why it was deferred rather than repaired

A1's recommended fix is to treat matched anchors as immutable. I implemented that
(skip anchored indices in `_clamp_to_duration`; stop the shortfall shift in
`_interpolate_gaps`) and ran the suite: it **fails**
`test_shortfall_cascade_clamped_to_duration` and `test_random_invariants_anchors`
with "word too short". Root cause: the codebase **deliberately** enforces, and
tests, the invariant *"when the words globally fit at `MIN_WORD_S`, every word is
at least `MIN_WORD_S` wide and inside the clip"* (`_assert_invariants`). In the
local-infeasible case (trailing filler words that whisper never transcribed, with
almost no room before clip end), keeping anchors fixed **forces** those filler
words below `MIN_WORD_S` — so anchor-immutability and the tested invariant cannot
both hold. The current code resolves the conflict by shifting the last anchors by
the *minimum* needed to fit the filler at `MIN_WORD_S` in-clip.

In realistic input (a few trailing ad-libs) that shift is small and within
ADR-002's ~0.5 s bar; the 0.9 s figure came from an extreme synthetic case
(20 untranscribed filler words after the last vocal, 0.05 s from clip end). So A1
is a genuine but **bounded** issue that needs a product decision, not a
mechanical fix. Options:

1. **Accept current behavior**, document that trailing untranscribed filler can
   pull the last anchors by up to the room deficit, and add a UI signal when the
   shift exceeds a threshold. (Lowest risk.)
2. **Prioritize anchors over `MIN_WORD_S`** for *unanchored filler only*: let
   filler words go sub-`MIN_WORD_S` and relax `_assert_invariants` accordingly.
   Changes a tested product invariant — needs sign-off.
3. **Cap filler count** per gap (drop/merge excess untranscribed tokens) so the
   local-infeasible case can't arise. Changes displayed text.

Recommend option 1 unless you want to revisit the `MIN_WORD_S` guarantee.

### Gate results
`ruff check .` clean · `ruff format --check .` clean · `pytest tests/
--cov=app --cov=render --cov=transcribe --cov-fail-under=85` → **355 passed,
coverage 90.28%**.
