# ADR-001: Subject-focused 9:16 crop for landscape input

**Status:** ACCEPTED — ratified by Finn 2026-09-14; Level-5 motion tuning ratified 2026-09-20. Reverses SPEC.md D2 and D12 for landscape input and adds D15.
**Date:** 2026-09-14; amended 2026-09-20
**Deciders:** Finn (maintainer)

## Context

SPEC D2 says vertical-only; landscape is deferred. D12 says blur-pad, never
crop, "to avoid the edge-crop failure fought in RicePoster". Finn now wants
16:9 interview footage to become a 9:16 clip that keeps the speaker in frame.

The cited failure needs correction. RicePoster's CHANGELOG (2026-07-27)
withdrew the edge-crop investigation. The edge loss was TikTok's own
rendering across phone viewports. It is handled by safe margins in the clip.
The lesson that survives is: **platforms trim edges variably, so the subject
must sit inside a safe zone.** Blur-pad satisfied that by never moving
content near the edge. A crop can satisfy it too, with a rule.

RiceClipper is local-first and has no network path except the deferred
header agent. Its render is one ffmpeg `filter_complex` per clip. Caption
geometry is fixed to a 1080x1920 canvas.

## Decision

Add a **subject crop** geometry mode for landscape input. It is local only,
single-subject, and falls back to blur-pad when detection is weak.

1. **Detection.** OpenCV YuNet face detector, ONNX model vendored in the
   repo. Sample at 5 fps on frames scaled to 640 px wide. No hosted model.
2. **Framing.** A full-height 9:16 window slides horizontally. A strong lock
   holds inside an outer 20% window-width zone and settles ordinary corrections
   at an inner 10% boundary. Those corrections interpolate at 30 Hz. Scene
   cuts, inferred face jumps, and returns after track loss remain immediate.
3. **Safe zone.** The tracked face center must stay inside the central 70% of
   the window. This replaces "never crop" as the edge-loss safeguard.
4. **Fallback.** If a face is present in under 80% of samples, or the safe
   zone check fails, the clip blur-pads. The reason is stored on the job.
5. **Control.** A per-clip `geometry` setting: `auto` (default), `blur_pad`,
   `crop`. RiceSearcher supplies nothing. Crop stays in Clipper (Searcher
   ADR-001 Q1).
6. **Kill criterion.** Six maintainer-supplied fixture clips. If fewer than
   five pass the safe-zone check after two tuning rounds, the feature is
   dropped and D12 stands.

## Options considered

| Topic | Chosen | Rejected | Why |
|-------|--------|----------|-----|
| Detector | YuNet (OpenCV) | MediaPipe | Extra runtime, model download; YuNet is one ONNX file |
| Detector | YuNet | Haar cascade | Poor on profile faces and low light |
| Speaker | single, largest face | active-speaker (audio-visual) | Large model and risk; Finn chose single subject |
| Window | slow-moving with cap | fixed per clip | Fails when the speaker walks |
| Window | full height, no zoom | zoom to reposition vertically | Zoom changes caption and header geometry |
| Hosting | local only | hosted fallback | Adds a network path to a repo with none |
| Timing | analyze at ingest | analyze at render | Review card must show the decision before render |

## Consequences

**Easier**
- Interview footage becomes usable. The review card shows the crop decision.
- Caption and header geometry do not change. The canvas is still 1080x1920.

**Harder / to revisit**
- New dependency `opencv-python-headless`. Analysis adds CPU time at ingest.
- ffmpeg must drive `crop` x over time. The spec names a spike for this.
- Face vertical position is fixed by the source. A face inside the header
  zone or caption zone is flagged, not fixed.
- A two-shot frames the larger face only. The other speaker is off frame.

## Routed forward

- Active-speaker switching. Only after single-subject earns trust.
- Zoom for vertical repositioning. Needs caption geometry work.
- Preview of the crop window in the review card before render.

## Action items

1. [x] Finn ratified 2026-09-14; SPEC.md §2, §4 step 2, §7, D2, D12, D15 updated.
2. [x] Issue #20 opened and the initial implementation shipped in PR #21 from
   `docs/design/subject-crop-spec.md`.
3. [x] The six-role landscape fixture gate was supplied and reviewed for the
   initial implementation; later music/interview tuning comparisons used
   maintainer-local sources and remained gitignored.

## Tuning round 2 (2026-09-15, approved by Finn)

Round 1 measured the ratified values on five music-video clips and three
interview clips. Three causes of failure: scene cuts missed at threshold 0.4,
close-up faces wider than the safe zone, and window lag on fast motion.
Changes: detector threshold 0.5, scene threshold 0.3, inferred cuts from a
face jump over 15% of width, no smoothing, pan cap 50% of width per second,
safe check on the face center. Thresholds 0.80 and 0.95 stand. Music-video
content stays out of the auto path; see Issue #22.

## Tuning round 3 (2026-09-20, approved by Finn)

Two paired five-level sweeps used the same music and interview sources and
reused one face track and cut list per source. Scaling the old response factor
did not materially reduce jitter because ffmpeg still applied discrete 5 Hz
position changes; slower response could spread one correction across several
visible steps. The second sweep tested continuous interpolation and progressively
stronger outer/inner lock zones. Finn selected Level 5 universally: outer zone
20% of `window_w`, settle zone 10%, and 30 Hz interpolation for ordinary motion.
Confirmed cuts, inferred face jumps, and loss returns still snap. Face selection,
scene thresholds, pan cap, and safe-rate rules are unchanged.
