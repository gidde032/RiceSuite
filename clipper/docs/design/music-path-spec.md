# Music-content path — design spec

Status: **RATIFIED 2026-09-15 (ADR-002 ACCEPTED, both parts). Implementation authority comes from the owning GitHub Issue.**
Date: 2026-09-15. Companion: `../adr/ADR-002-music-path.md`. Builds on `subject-crop-spec.md`.

## Purpose

Give music-video clips a usable crop and usable captions. Part 1 is a follow
framing profile that holds through faceless spans. Part 2 is a lyric fallback
that replaces a poor transcription with pasted lyrics on borrowed timings.
Both are local. Part 1 ships first.

## Consumers

- The maintainer, through the review card and the render.
- RicePoster. No change. Same 1080x1920 mp4, same handoff.
- RiceSearcher. No change. The manifest carries no content or lyric field.

## Contract

### Models (`app/models.py`)

- `Content = Literal["speech", "music"]`. `RenderRequest.content: Content = "speech"`.
- `CropPlan.profile: Content = "speech"`.
- `CropReason` gains `"hold_static"` (music profile, no face ever found).
- `JobState.music_plan: CropPlan | None = None`. Set with `crop_plan` for landscape input.
- `Word.line_start: bool = False`. Set by the aligner on the first word of each lyric line. `HandoffClip` is unchanged.
- `LyricsRequest = {lyrics: str}`. `LyricsResult = {words: list[Word], anchor_rate: float, method: "anchors" | "even_fill"}`.

### Framing profile (`render/framing.py`)

`plan_crop(track, cuts, source_w, source_h, *, sample_times, profile="speech")`.
The speech profile is byte-for-byte the ratified behavior. The music profile
differs only in these rules:

- Scene cuts: use cuts with score above `SCENE_MIN_MUSIC = 0.2`. Speech keeps 0.3.
- Track loss: hold `x` for any length of loss. `LOSS_S` still marks the loss so a returning face snaps.
- Decision: always `crop`, reason `ok`. No `FACE_RATE_MIN` or `SAFE_RATE_MIN` gate. `face_rate` and `safe_rate` are still computed and reported.
- No face in any sample: one centered sample, decision `crop`, reason `hold_static`.
- Dead zone, pan cap, jump cut, and safe-zone measurement stay as ratified.
- Group shots belong to `subject.py` (below). Framing sees one box per sample.

### Detection (`render/subject.py`)

- One scene pass per clip. Run `select='gt(scene,0.2)'` and record the score per cut with `metadata=print:key=lavfi.scene_score` before `showinfo`. Return `list[(t, score)]`. `plan_crop` filters by profile. **Spike S2, first task:** prove the score prints on ffmpeg 9.0.1. If it fails, run a second pass at 0.2 for the music plan only.
- Group shots: after a cut, take the largest box. Between cuts, prefer the box whose center is nearest the previous target, inside `CONTINUITY` (0.15 of width); else the largest. This replaces the current "largest inside the band" rule for both profiles. Interview fixtures must still pass 4 of 4.
- `build_plan` returns `(crop_plan, music_plan)` from one track and one scene pass. A failure stores `analysis_failed` on both.

### Lyric alignment (`transcribe/lyrics.py`, pure)

`align(lyrics: str, reference: list[Word], duration: float) -> LyricsResult`.

1. Split lyrics into lines, then tokens. Normalize: lowercase, strip punctuation. Keep the original token text for output.
2. Match lyric tokens to reference tokens with `difflib.SequenceMatcher` on the normalized lists. Matched tokens are anchors. An anchor takes the reference word's `start` and `end`.
3. Vocal span: first reference start to last reference end. No reference words: `0` to `duration`.
4. `anchor_rate` = anchors / lyric tokens. If `anchor_rate < 0.25`, method is `even_fill`: spread lines across the vocal span weighted by character count, then words inside each line the same way (amended 2026-09-16, review 2A-1; was "evenly").
5. Else method is `anchors`. Words between two anchors spread evenly across the gap, weighted by character count. Words before the first anchor spread from the vocal span start; words after the last spread to the vocal span end.
6. Every word gets `end > start`. Words never overlap. Order is lyric order.
7. The first token of each line sets `line_start = True`.

`group_words` (`transcribe/phrasing.py`) starts a new phrase when `line_start` is true. Lines longer than `max_words` still split as today.

### Endpoints (`app/main.py`)

- `POST /api/jobs/{id}/lyrics` with `LyricsRequest`. Runs `align` against `job.words` and `job.info.duration`. Stores the result on `job.words` and returns `LyricsResult`. 409 while the job is active. 422 on an empty block.
- `POST /api/jobs/{id}/transcribe` is unchanged and restores whisper words.
- `render_job`: resolve geometry from `music_plan` when `req.content == "music"` and the input is landscape. Overrides `blur_pad` and `crop` behave as today. Vertical input ignores `content` for geometry.

### Pipeline placement

- Alignment sits between transcribe and the review gate. It is a review-gate action, not a render step.
- Render is unchanged. `req.words` already carry lyric text and timing. The ASS build and phrasing consume them as today.

### Review UI (`web/`)

- A `Content` radio row on every job: Speech, Music. Slate radio cards, not remembered per slot. Default Speech.
- The Geometry Auto card gains the fallback reason: `blur-pad · face 42% · low face rate`. When Content is Music on a landscape job, the Auto card shows the music plan: `crop · face 42% · holds`, or `crop · static centered`.
- Music selected: a lyric textarea and an `Align` button appear under the transcript. Align posts the block, replaces the editable words, and shows a badge: `aligned · 61% anchors` or `even fill`. Speech selected hides the textarea. Pasted text persists on the card for the session.
- The render payload carries `content`.

## Boundaries

- No new model. No torch. No network.
- No sidecar lyric files. Pasted text only.
- Single subject. No active-speaker switching.
- No change to caption geometry, `PlayResX/Y`, or margins.
- No change to the RiceSearcher manifest or the RicePoster handoff.
- SPEC.md changes require explicit maintainer approval.

## First reliability risk

Whisper timings on sung vocals drift, so anchors land on the wrong beat and
the highlight walks out of step with the vocal. The anchor threshold, the
badge, and the fixture attestation bound this. If it fails, forced alignment
is the routed-forward option, not a hot fix.

## Build order for a cheap model

1. **M0 measure.** `scripts/crop_check.py` gains `--profile speech|music` and `--roles none` to skip the role check. Run both profiles on `fixtures/` at current `main`. Post the JSON to the Issue. This is the baseline; do not tune before it exists.
2. **M1 framing profile.** `profile` argument, `hold_static`, music-profile rules, `CropPlan.profile`. Tests from committed JSON tracks: faceless span hold, no face static, cut snap at 0.2, speech profile unchanged.
3. **M2 detection.** Spike S2. Scene scores. Group-shot rule. `build_plan` returns both plans. Ingest stores `music_plan`. Re-run the interview fixtures; 4 of 4 must hold.
4. **M3 control and UI.** `content` field. Resolve geometry from `music_plan`. Content row, Auto-card reason, music summary.
5. **M4 fixture check, part 1.** Finn runs M0 again on `fixtures/`. Pass rule below. Gate for part 2.
6. **M5 aligner.** `transcribe/lyrics.py`, `Word.line_start`, phrasing break, endpoint. Tests: exact match, partial match with interpolation, no reference words, below-threshold even fill, non-overlap invariant.
7. **M6 lyric UI.** Textarea, Align, badge, restore on re-transcribe.
8. **M7 fixture check, part 2.** Finn pastes lyrics for the five music clips and renders each with a lyric preset.

M1 to M4 are one PR. M5 to M7 are a second PR after the part-1 gate.

## Verification and gates

- `ruff check .`, `ruff format --check .`, `node --check web/app.js`, `git diff --check`.
- `pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85`. Update `test_gates.py` if the smoke count changes.
- Framing tests: the speech profile output is identical to the pre-change output on every committed track. The music profile never returns `blur_pad` except `analysis_failed`.
- Aligner tests: word order is lyric order; `end > start` for every word; no overlap; `anchor_rate` is exact on a hand-built case.
- Part 1 pass rule (five music clips plus `music-noface.mov`): every clip yields a music plan with decision `crop`. `safe_rate >= 0.85` over face samples on at least 4 of 5. `music-noface.mov` yields `hold_static`. Finn reviews the six contact sheets and attests: the subject sits inside the safe zone where a face is present, cuts snap, no jitter.
- Part 1 kill criterion: fewer than 4 of 5 pass after one tuning round. Then the music profile is dropped and blur-pad stands for music.
- Part 2 pass rule: on the five music clips, `anchor_rate` is reported per clip, and Finn attests the highlight tracks the vocal within about half a second on at least 3 of 5.
- Part 2 kill criterion: the highlight is visibly off on more than 2 of 5. Then the aligner is dropped and forced alignment is opened as a Wave-2 Issue.
