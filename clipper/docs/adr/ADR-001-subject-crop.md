# ADR-001: Subject-focused 9:16 crop for landscape input

**Status:** ACCEPTED — ratified by Finn 2026-09-14. Reverses SPEC.md D2 and D12 for landscape input and adds D15.
**Date:** 2026-09-14
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
2. **Framing.** A full-height 9:16 window slides horizontally. Dead zone,
   exponential smoothing, and a pan speed cap keep it calm. It snaps only
   at a scene cut or after a track loss.
3. **Safe zone.** The tracked face box must stay inside the central 70% of
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
2. [ ] Open one Issue. Build from `docs/design/subject-crop-spec.md`.
3. [ ] Finn supplies six landscape fixture clips to `fixtures/landscape/` (gitignored).
