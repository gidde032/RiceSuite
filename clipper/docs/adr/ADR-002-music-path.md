# ADR-002: Music-content path — follow framing profile and lyric fallback

**Status:** ACCEPTED — ratified by Finn 2026-09-15 (build both parts). Extends ADR-001. Touches SPEC.md D3, D4, D15; adds D16.
**Date:** 2026-09-15
**Deciders:** Finn (maintainer)

## Context

ADR-001 tuned the subject crop for interview footage. Fixture run 1 on five
music-video clips (Issue #22, PR #21) resolved every clip to `blur_pad` under
`auto`. Face rate was 13–79% at threshold 0.7 and 29–88% at 0.5. Faces are
absent for long spans: b-roll, backs, dark group shots. Scene cuts went
undetected on green-tinted footage; three of five clips reported zero cuts.
The 80% face-rate floor is correct for interviews and wrong for this content.

Transcription also degrades on sung vocals. Whisper text is poor, so the
word-highlight captions carry wrong words. The four lyric caption presets
exist, but they have nothing accurate to display.

Constraints hold: local only, no network, no change to the RiceSearcher
manifest or the RicePoster handoff.

## Decision

Add a user-selectable **music path** with two parts. Part 1 ships first.

1. **Control.** One per-clip setting `content: speech | music`, default
   `speech`. It is not remembered per slot. Content belongs to the clip.
2. **Follow framing profile** (landscape input only). `plan_crop` gains a
   `profile` argument. The music profile: no face-rate gate; hold the window
   through faceless spans; snap on cuts and on face return; static centered
   window when no face is ever found; scene threshold 0.2 instead of 0.3.
   `CropPlan` gains a `profile` field. There is no second plan type.
3. **Both plans at ingest.** Landscape analysis runs once and produces
   `crop_plan` (speech) and `music_plan` (music) from the same track and the
   same scene pass. The review card can show both before render.
4. **Group shots.** Between cuts, keep the face nearest the previous target.
   After a cut, take the largest face. One rule, one continuity band.
5. **Lyric fallback** (any input). An optional pasted lyric block. Align
   replaces the transcript with the lyric words. Whisper words serve as timing
   anchors only. Unmatched words interpolate between anchors. Word-level
   highlight survives. No new model.
6. **Kill criteria.** Framing: fewer than 4 of 5 music fixtures pass after one
   tuning round. Lyrics: Finn judges the highlight visibly off on more than 2
   of 5 fixtures. A killed part is dropped; the other part stands.

## Options considered

| Topic | Chosen | Rejected | Why |
|-------|--------|----------|-----|
| Faceless spans | hold last x | blur-pad the clip | Music footage is chosen on purpose; a hold keeps the last framed subject |
| No face ever | static centered crop | blur-pad | Finn chose full-bleed; blur-pad stays one click away |
| Cut detection | scene 0.2 in the same pass, scores recorded | histogram detector in the decode loop | Zero extra cost; the detector is routed forward if 0.2 still misses cuts |
| Group shots | nearest previous, largest after cut | centroid of all faces | A centroid puts every face at an edge |
| Alignment | whisper anchors + interpolation | forced alignment (wav2vec2, ~360 MB) | New model and torch dependency; anchors need no model |
| Alignment | whisper anchors | even fill only | Even fill is the degrade path, not the design |
| Lyric input | pasted block | sidecar file | A sidecar touches the RiceSearcher pickup path |
| Control | one `content` setting | two independent settings | Content type drives both parts; the lyric block is data |
| ADR shape | ADR-002 | amend ADR-001 | ADR-001 is ratified and unchanged; this adds a caption-source decision it never covered |

## Consequences

**Easier**
- Music clips crop under a profile that matches their cut rhythm.
- Lyric presets display correct words with usable timing.
- No new dependency. Alignment is standard library.

**Harder / to revisit**
- Whisper timings on sung vocals may drift. The anchor rate badge exposes
  this; the kill criterion bounds it.
- Analysis of landscape input now produces two plans. Memory per job grows by
  one sample list.
- `Word` gains an optional `line_start` flag so phrases follow lyric lines.

## Routed forward

- Forced alignment as a Wave-2 option if the anchor method is killed.
- Histogram cut detector if scene 0.2 still misses cuts on filtered footage.
- Sidecar lyric files once the RiceSearcher manifest gains a lyric field.
- Per-slot memory of `content` if a slot becomes a music-only account.

## Action items

1. [x] Finn ratified 2026-09-15; SPEC.md §2, §4 steps 2 and 4, §7, D3, D4, D15, D16 updated.
2. [ ] Open one Issue from `docs/design/music-path-spec.md`. Build M0 first.
3. [ ] Finn supplies lyrics for the five music fixtures (local, gitignored).
