# Subject-focused 9:16 crop — design spec

Status: **RATIFIED 2026-09-14 (ADR-001 ACCEPTED). Implementation authority comes from the owning GitHub Issue.**
Date: 2026-09-14. Companion: `../adr/ADR-001-subject-crop.md`.

## Purpose

Turn landscape input into a 1080x1920 clip that keeps one speaker in frame.
Replace blur-pad for that input when detection is strong. Keep blur-pad as
the fallback and as an explicit per-clip choice.

## Consumers

- The maintainer, through the review card and the render.
- RicePoster. No change. It receives the same 1080x1920 mp4.
- RiceSearcher. No change. The manifest carries no geometry field.

## Contract

### Modules

- `render/subject.py` — detection and tracking. Input: source path, probe info. Output: a `Track` (list of `{t, cx, cy, w, h}` or `null` per sample) plus scene-cut times.
- `render/framing.py` — pure. Input: `Track`, cuts, source size. Output: a `CropPlan`: `{decision, reason, face_rate, safe_rate, window_w, window_h, samples: [{t, x}]}`.
- `render/geometry.py` — gains `crop_statements(plan, input_label, out_label)`.
- `render/models/face_detection_yunet_2023mar.onnx` — vendored, Apache-2.0, about 230 KB.

### Detection (`subject.py`)

- Trigger: `info.width > info.height` only. Vertical and square input never run it.
- Decode with `cv2.VideoCapture`. Sample every `round(fps / 5)` frames. Scale to 640 px wide for the detector. Map boxes back to source pixels.
- YuNet confidence threshold 0.7. Keep the largest box per sample.
- Continuity: if a previous target exists and a box center lies within 15% of source width of it, prefer that box over a larger one.
- Track loss: no box for more than 1.0 s.
- Scene cuts: `ffmpeg -vf "select='gt(scene,0.4)',showinfo"` on the same source. Record `pts_time` values.
- Runs under `run_owned` with timeout `max(60, duration * 2)`. Target: under 10 s for a 60 s 1080p clip.
- Runs at ingest inside the existing transcribe step, after probe. Result is stored on the job as `crop_plan`. A failure stores `decision: blur_pad, reason: analysis_failed`. It never fails the job.

### Framing policy (`framing.py`)

- Window: `window_h = source_h`, `window_w = round(source_h * 9 / 16)`, even. For 1920x1080 input that is 608x1080.
- Target x per sample: `cx - window_w / 2`, clamped to `[0, source_w - window_w]`.
- Dead zone: if the target lies within 10% of `window_w` of the current x, hold.
- Smoothing: `x += 0.15 * (target - x)` per sample.
- Pan cap: at most 8% of `source_w` per second.
- Scene cut: on the first sample after a cut, set `x = target` with no smoothing.
- Track loss: hold x. When a face returns after a loss, snap to it.
- Missing samples inside a loss are filled by hold. The plan has one `x` per sample.

### Decision (`auto`)

- `face_rate` = samples with a face / all samples.
- `safe_rate` = samples where the face box lies inside the central 70% of the window / samples with a face.
- `decision = crop` when `face_rate >= 0.80` and `safe_rate >= 0.95`. Else `blur_pad`. `reason` names the failed threshold.
- Warn, do not block, when the face box intersects the header zone (top 450 px of output) or the caption zone (bottom 540 px of output) in over 20% of samples. Store `warning` on the plan.

### Per-clip control

- `RenderRequest.geometry: Literal["auto", "blur_pad", "crop"] = "auto"`.
- `crop` with a plan whose `decision` is `blur_pad` still crops. The user overrides on purpose. `crop` with no plan (vertical input) is a 400.
- `HandoffClip` and the RicePoster manifest are unchanged.

### Pipeline placement (`pipeline.py` step 3)

- Vertical input: unchanged pass-through.
- Landscape and resolved `blur_pad`: unchanged `blur_pad_statements`.
- Resolved `crop`: write `crop.cmd` to the job dir. Statements: `[0:v]sendcmd=f=crop.cmd,crop=W:H:0:0,scale=1080:1920[base]`. Then `[base]subtitles=captions.ass...` as today. Header overlay unchanged.
- `crop.cmd` lines: `<t> crop x <x>` per sample. Bare filename, cwd is the job dir, same as the ASS.
- **Spike S1, first task:** prove `sendcmd` drives `crop` x on ffmpeg 9.0.1 with a two-line command file. If it fails, build `x='if(lt(t,T1),X1,if(lt(t,T2),X2,...))'` from the plan, subsampled to at most 64 segments. Record the result in `docs/spikes/crop-sendcmd.md`.
- Caption geometry: no change. `PlayResX/Y` stay 1080x1920. Margins stay.

### Review UI

- Landscape jobs show a `Geometry` radio row: Auto, Blur-pad, Crop. Slate radio cards, same pattern as header style.
- The Auto card shows the plan: `crop · face 96% · safe 99%` or `blur-pad · face 41%`. A warning shows a small badge.
- Vertical jobs show no row.

## Boundaries

- Single subject. No active-speaker switching. A two-shot frames the larger face.
- No zoom. Vertical position is the source's.
- No hosted model. No network.
- No crop preview before render in this phase.
- No change to the RiceSearcher manifest or the RicePoster handoff.
- No SPEC.md edit by the agent.

## First reliability risk

The tracker locks onto the wrong face, or the window jitters. Both put the
speaker at the edge, which TikTok then trims. The dead zone, pan cap,
largest-face rule, and the safe-zone gate are the mitigations. The fixture
run proves them.

## Build order for a cheap model

1. **F1 framing.** `framing.py` pure functions. Tests from committed JSON tracks in `tests/fixtures/tracks/`: static, walking, two-shot, cut-heavy, no-face, lost-then-found.
2. **F2 spike + geometry.** Spike S1. `crop_statements`. Pipeline branch with a forced plan. Test the command string.
3. **F3 detector.** `subject.py`. Ingest hook. `crop_plan` on `JobState`. Unit tests mock the detector.
4. **F4 control + UI.** `geometry` field. Radio row. Auto badge.
5. **F5 fixture check.** `scripts/crop_check.py`: for each clip in `fixtures/landscape/`, write a 12-frame contact sheet with the window and face box drawn, plus a JSON report of `face_rate`, `safe_rate`, and max pan speed.

F1 to F4 are one PR. F5 runs locally by the maintainer before the PR is ready.

## Verification and gates

- `ruff check .`, `ruff format --check .`, `node --check web/app.js`, `git diff --check`.
- `pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85`. Update `test_gates.py` if the smoke count changes.
- Framing tests: pan speed never exceeds the cap; a cut snaps; a loss holds.
- Fixture set (maintainer, gitignored): six clips of 20 to 60 s. Static single speaker; walking speaker; two-shot; cut-heavy; b-roll with no face; low light.
- Pass: the four face clips reach `safe_rate >= 0.95`. The no-face clip falls back. The two-shot holds one face without a switch.
- Finn reviews the six contact sheets. The report JSON is attached to the PR.
- Kill criterion: fewer than five of six pass after two tuning rounds. Then D12 stands.
