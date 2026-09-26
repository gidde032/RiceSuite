#!/usr/bin/env python3
"""Render a controlled subject-crop tuning comparison.

Each source is analyzed once. The same detected face track and scene cuts are
then framed and rendered at five levels for either the response or lock sweep.
Confirmed scene cuts, face jumps, and returns after a long loss still snap
immediately.

Usage:
    python scripts/crop_tuning_sweep.py \
      --music /path/to/music.mp4 \
      --interview /path/to/interview.mp4 \
      --sweep lock \
      --out outputs/crop-tuning
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import asdict
from itertools import pairwise
from pathlib import Path

# Ensure project root is importable when invoked as a script.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.models import CropPlan, CropSample, RenderRequest, TrackSample  # noqa: E402
from app.probe import MediaInfo, probe  # noqa: E402
from render import framing, subject  # noqa: E402
from render.pipeline import render  # noqa: E402

RESPONSE_LEVELS = (
    ("L1_current", 1.00, framing.DEAD_ZONE, 0.0, False),
    ("L2_slight", 0.70, framing.DEAD_ZONE, 0.0, False),
    ("L3_balanced", 0.45, framing.DEAD_ZONE, 0.0, False),
    ("L4_smooth", 0.25, framing.DEAD_ZONE, 0.0, False),
    ("L5_original", 0.15, framing.DEAD_ZONE, 0.0, False),
)

LOCK_LEVELS = (
    ("L1_current", 1.00, 0.10, 0.000, False),
    ("L2_interpolated", 1.00, 0.10, 0.000, True),
    ("L3_mild_lock", 1.00, 0.10, 0.050, True),
    ("L4_medium_lock", 1.00, 0.15, 0.075, True),
    ("L5_strong_lock", 1.00, 0.20, 0.100, True),
)


def _analyze(
    source: Path, info: MediaInfo
) -> tuple[list[TrackSample | None], list[float], list[tuple[float, float]]]:
    """Return one reusable track, its timestamps, and scored scene cuts."""
    if info.width <= info.height:
        raise ValueError(f"source is not landscape: {info.width}x{info.height}")
    if info.field_order not in {"progressive", "unknown"}:
        raise ValueError("interlaced input is not safe for subject detection")

    timeout = subject.analysis_timeout(info.duration)
    deadline = time.monotonic() + timeout
    cuts = subject.scene_cuts(source, timeout)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("analysis deadline passed before face detection")
    track, sample_times = subject.detect_track_owned(source, info, remaining, cuts=cuts)
    return track, sample_times, cuts


def _motion_metrics(xs: list[int]) -> dict[str, int]:
    deltas = [abs(cur - prev) for prev, cur in pairwise(xs)]
    return {
        "moving_samples": sum(delta > 0 for delta in deltas),
        "total_motion_px": sum(deltas),
        "max_sample_step_px": max(deltas, default=0),
    }


def _snap_indices(
    track: list[TrackSample | None],
    sample_times: list[float],
    cuts: list[tuple[float, float]],
    source_w: int,
    profile: str,
) -> set[int]:
    """Return indices where framing intentionally performs an immediate snap."""
    threshold = (
        framing.SCENE_MIN_MUSIC if profile == "music" else framing.SCENE_MIN_SPEECH
    )
    cut_times = [t for t, score in cuts if score > threshold]
    snaps: set[int] = set()
    prev_t: float | None = None
    last_face_t: float | None = None
    prev_face_cx: float | None = None
    lost = False
    pending_cut = False

    for i, (sample, t_i) in enumerate(zip(track, sample_times, strict=True)):
        has_face = sample is not None
        scene_cut = prev_t is not None and any(
            prev_t < cut_time <= t_i for cut_time in cut_times
        )
        pending_cut = pending_cut or scene_cut
        face_jump = (
            has_face
            and prev_face_cx is not None
            and abs(sample.cx - prev_face_cx) > framing.JUMP_CUT * source_w
        )
        return_after_loss = has_face and (
            (last_face_t is None and t_i > framing.LOSS_S)
            or (last_face_t is not None and (t_i - last_face_t) > framing.LOSS_S)
        )

        if (
            i > 0
            and has_face
            and (pending_cut or face_jump or lost or return_after_loss)
        ):
            snaps.add(i)

        if has_face:
            if pending_cut or face_jump:
                lost = False
                pending_cut = False
            elif lost or return_after_loss:
                lost = False
            last_face_t = t_i
            prev_face_cx = sample.cx
        elif last_face_t is not None and (t_i - last_face_t) > framing.LOSS_S:
            lost = True
        prev_t = t_i

    return snaps


def _interpolate_plan(
    plan: CropPlan, snap_indices: set[int], fps: int = 30
) -> CropPlan:
    """Densify ordinary moves while retaining intentional snaps as hard steps."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    if len(plan.samples) < 2:
        return plan.model_copy(deep=True)

    dense = [plan.samples[0]]
    for i, (previous, current) in enumerate(pairwise(plan.samples), start=1):
        dt = current.t - previous.t
        if i in snap_indices or current.x == previous.x or dt <= 0:
            dense.append(current)
            continue

        steps = max(1, round(dt * fps))
        for step in range(1, steps + 1):
            if step == steps:
                dense.append(current)
                continue
            fraction = step / steps
            t_i = previous.t + dt * fraction
            raw_x = round(previous.x + (current.x - previous.x) * fraction)
            even_x = raw_x if raw_x % 2 == 0 else raw_x - 1
            dense.append(CropSample(t=t_i, x=even_x))

    return plan.model_copy(update={"samples": dense})


def _render_source(
    label: str,
    profile: str,
    source: Path,
    out_dir: Path,
    sweep: str,
) -> dict:
    info = probe(str(source))
    print(
        f"Analyzing {label}: {source.name} "
        f"({info.width}x{info.height}, {info.duration:.2f}s)",
        flush=True,
    )
    started = time.monotonic()
    track, sample_times, cuts = _analyze(source, info)
    analysis_s = time.monotonic() - started
    snap_indices = _snap_indices(track, sample_times, cuts, info.width, profile)

    result = {
        "label": label,
        "profile": profile,
        "source": str(source.resolve()),
        "media": asdict(info),
        "analysis_s": round(analysis_s, 3),
        "track_samples": len(track),
        "face_samples": sum(sample is not None for sample in track),
        "detected_cuts": len(cuts),
        "snap_samples": len(snap_indices),
        "levels": [],
    }

    request = RenderRequest(
        captions_on=False,
        geometry="crop",
        content=profile,
    )
    levels = LOCK_LEVELS if sweep == "lock" else RESPONSE_LEVELS
    for level, response, dead_zone, settle_zone, interpolate in levels:
        plan = framing.plan_crop(
            track,
            cuts,
            info.width,
            info.height,
            sample_times=sample_times,
            profile=profile,
            motion_response=response,
            dead_zone=dead_zone,
            settle_zone=settle_zone,
            interpolation_fps=0,
        )
        render_plan = _interpolate_plan(plan, snap_indices) if interpolate else plan
        forced_plan = render_plan.model_copy(update={"decision": "crop"})
        filename = f"{label}_{level}_r{round(response * 100):03d}.mp4"
        destination = out_dir / filename
        print(f"  Rendering {level} ({response:.2f}) -> {filename}", flush=True)
        render_started = time.monotonic()
        with tempfile.TemporaryDirectory(
            prefix=f".{label}-{level}-", dir=out_dir
        ) as raw:
            rendered = render(
                raw,
                source,
                info,
                request,
                plan=forced_plan,
            )
            shutil.move(rendered, destination)
        level_result = {
            "level": level,
            "motion_response": response,
            "dead_zone": dead_zone,
            "settle_zone": settle_zone,
            "interpolated": interpolate,
            "output": filename,
            "decision_before_forced_crop": plan.decision,
            "reason": plan.reason,
            "face_rate": round(plan.face_rate, 6),
            "safe_rate": round(plan.safe_rate, 6),
            "render_s": round(time.monotonic() - render_started, 3),
            "plan_samples": len(plan.samples),
            "command_samples": len(render_plan.samples),
        }
        level_result.update(_motion_metrics([sample.x for sample in plan.samples]))
        result["levels"].append(level_result)

    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--music", required=True, type=Path)
    parser.add_argument("--interview", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument(
        "--sweep",
        choices=["response", "lock"],
        default="response",
        help="Tuning axis to render (default: response).",
    )
    args = parser.parse_args(argv)

    for source in (args.music, args.interview):
        if not source.is_file():
            parser.error(f"source does not exist: {source}")

    args.out.mkdir(parents=True, exist_ok=True)
    report = {
        "purpose": f"subject-crop {args.sweep} sweep",
        "invariants": {
            "face_track_reused_across_levels": True,
            "scene_cuts_reused_across_levels": True,
            "dead_zone": framing.DEAD_ZONE,
            "pan_cap": framing.PAN_CAP,
            "jump_cut": framing.JUMP_CUT,
            "loss_s": framing.LOSS_S,
            "sample_fps": framing.SAMPLE_FPS,
            "cut_and_loss_snaps": "unchanged",
            "sweep": args.sweep,
        },
        "sources": [],
    }
    report["sources"].append(
        _render_source("music", "music", args.music, args.out, args.sweep)
    )
    report["sources"].append(
        _render_source("interview", "speech", args.interview, args.out, args.sweep)
    )

    report_path = args.out / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Done. Review videos and report: {args.out.resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
