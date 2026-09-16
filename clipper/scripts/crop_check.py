#!/usr/bin/env python3
"""Contact-sheet and report generator for the subject-crop fixture set.

For each landscape clip in a directory: probe, detect, frame, then write a
12-frame contact sheet with the crop window (green) and nearest face box (red),
plus a JSON report row.  Not under coverage; not in CI.  Maintainer runs it.

Usage:
    python scripts/crop_check.py [fixtures/landscape] [--contact-sheets-approved]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

# Ensure project root is importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

from app.probe import MediaInfo, probe  # noqa: E402
from render import framing, subject  # noqa: E402

SUFFIXES = {".mp4", ".mov"}
GRID_COLS = 4
GRID_ROWS = 3
THUMB_W = 480
GREEN = (0, 255, 0)
RED = (0, 0, 255)
THICKNESS = 2
ANALYSIS_TARGET_S = 10.0
REQUIRED_ROLES = (
    "static",
    "walking",
    "two_shot",
    "cut_heavy",
    "no_face",
    "low_light",
)


def _cv_modules():
    """Import optional fixture-run dependencies only when the gate executes."""
    import cv2
    import numpy as np

    return cv2, np


def _role_for(source: Path) -> str | None:
    """Map a fixture filename to one of the six ratified fixture roles."""
    name = source.stem.lower().replace("-", "_").replace(" ", "_")
    compact = name.replace("_", "")
    aliases = {
        "static": ("static",),
        "walking": ("walking",),
        "two_shot": ("two_shot", "twoshot"),
        "cut_heavy": ("cut_heavy", "cutheavy"),
        "no_face": ("no_face", "noface", "b_roll", "broll"),
        "low_light": ("low_light", "lowlight"),
    }
    matches = [
        role
        for role, tokens in aliases.items()
        if any(token in name or token.replace("_", "") in compact for token in tokens)
    ]
    return matches[0] if len(matches) == 1 else None


def _fixture_roles(clips: list[Path]) -> dict[Path, str]:
    """Validate exactly one clip for every required fixture role."""
    if len(clips) != len(REQUIRED_ROLES):
        raise ValueError(f"expected exactly six fixture clips; found {len(clips)}")
    resolved: dict[Path, str] = {}
    seen: set[str] = set()
    for clip in clips:
        role = _role_for(clip)
        if role is None:
            raise ValueError(f"cannot determine fixture role from {clip.name}")
        if role in seen:
            raise ValueError(f"duplicate fixture role: {role}")
        resolved[clip] = role
        seen.add(role)
    missing = set(REQUIRED_ROLES) - seen
    if missing:
        raise ValueError(f"missing fixture roles: {', '.join(sorted(missing))}")
    return resolved


def _is_landscape(info: MediaInfo) -> bool:
    return info.width > info.height


def _grab_frames(
    source: Path, count: int, info: MediaInfo
) -> list[tuple[float, object]]:
    """Return up to *count* frames at equal time steps."""
    cv2, _np = _cv_modules()
    cap = cv2.VideoCapture(str(source))
    try:
        if info.rotation:
            cap.set(subject._CAP_PROP_ORIENTATION_AUTO, 1)
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            return []
        step = max(1, total // count)
        frames: list[tuple[float, object]] = []
        for i in range(count):
            idx = i * step
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            if (frame.shape[1], frame.shape[0]) != (info.width, info.height):
                frame = cv2.resize(frame, (info.width, info.height))
            reported_t = float(cap.get(cv2.CAP_PROP_POS_MSEC)) / 1000.0
            has_pts = math.isfinite(reported_t) and (reported_t > 0 or idx == 0)
            t = reported_t if has_pts else idx / fps
            frames.append((t, frame))
        return frames
    finally:
        cap.release()


def _nearest_track(
    t: float, track: list[subject.TrackSample | None]
) -> subject.TrackSample | None:
    """Return the track sample nearest in time that has a face."""
    best: subject.TrackSample | None = None
    best_dt = float("inf")
    for s in track:
        if s is None:
            continue
        dt = abs(s.t - t)
        if dt < best_dt:
            best_dt = dt
            best = s
    return best


def _nearest_crop_x(t: float, plan: framing.CropPlan) -> int | None:
    """Return the crop x nearest in time from the plan samples."""
    best_x: int | None = None
    best_dt = float("inf")
    for cs in plan.samples:
        dt = abs(cs.t - t)
        if dt < best_dt:
            best_dt = dt
            best_x = cs.x
    return best_x


def _draw_boxes(
    frame,
    t: float,
    track: list[subject.TrackSample | None],
    plan: framing.CropPlan,
    src_w: int,
    src_h: int,
) -> object:
    """Draw crop window (green) and nearest face box (red) on a copy."""
    cv2, _np = _cv_modules()
    out = frame.copy()
    h_frame, w_frame = out.shape[:2]
    sx = w_frame / src_w
    sy = h_frame / src_h

    crop_x = _nearest_crop_x(t, plan)
    if crop_x is not None:
        x1 = int(crop_x * sx)
        y1 = 0
        x2 = int((crop_x + plan.window_w) * sx)
        y2 = h_frame
        cv2.rectangle(out, (x1, y1), (x2, y2), GREEN, THICKNESS)

    face = _nearest_track(t, track)
    if face is not None:
        fx1 = int((face.cx - face.w / 2) * sx)
        fy1 = int((face.cy - face.h / 2) * sy)
        fx2 = int((face.cx + face.w / 2) * sx)
        fy2 = int((face.cy + face.h / 2) * sy)
        cv2.rectangle(out, (fx1, fy1), (fx2, fy2), RED, THICKNESS)

    return out


def _build_contact_sheet(
    source: Path,
    track: list[subject.TrackSample | None],
    plan: framing.CropPlan,
    info: MediaInfo,
) -> object:
    """Build a 4x3 contact sheet at THUMB_W per cell."""
    cv2, np = _cv_modules()
    count = GRID_COLS * GRID_ROWS
    frames = _grab_frames(source, count, info)
    if len(frames) != count:
        raise RuntimeError(
            f"contact-sheet decode produced {len(frames)} of {count} frames"
        )

    thumb_h = int(THUMB_W * info.height / info.width)
    cells: list[np.ndarray] = []
    for t, frame in frames:
        annotated = _draw_boxes(frame, t, track, plan, info.width, info.height)
        thumb = cv2.resize(annotated, (THUMB_W, thumb_h))
        cells.append(thumb)

    while len(cells) < count:
        cells.append(np.zeros((thumb_h, THUMB_W, 3), dtype=np.uint8))

    rows = []
    for r in range(GRID_ROWS):
        row_cells = cells[r * GRID_COLS : (r + 1) * GRID_COLS]
        rows.append(np.hstack(row_cells))
    return np.vstack(rows)


def _max_pan_px_per_s(plan: framing.CropPlan) -> float:
    """Return the maximum pan speed in pixels per second across samples."""
    if len(plan.samples) < 2:
        return 0.0
    worst = 0.0
    for i in range(1, len(plan.samples)):
        prev = plan.samples[i - 1]
        curr = plan.samples[i]
        dt = curr.t - prev.t
        if dt <= 0:
            continue
        speed = abs(curr.x - prev.x) / dt
        if speed > worst:
            worst = speed
    return round(worst, 1)


def _max_governed_pan_px_per_s(
    plan: framing.CropPlan,
    track: list[subject.TrackSample | None],
    cuts: list[tuple[float, float]],
    source_w: int,
) -> float:
    """Return worst non-snap pan speed; cuts and returns may snap by contract."""
    threshold = (
        framing.SCENE_MIN_MUSIC if plan.profile == "music" else framing.SCENE_MIN_SPEECH
    )
    cut_times = [t for t, sc in cuts if sc > threshold]
    if len(plan.samples) != len(track):
        return float("inf")
    worst = 0.0
    last_face_t: float | None = None
    prev_face_cx: float | None = None
    pending_cut = False
    for i, (crop, face) in enumerate(zip(plan.samples, track, strict=True)):
        if i:
            prev = plan.samples[i - 1]
            dt = crop.t - prev.t
            scene_cut = any(prev.t < cut <= crop.t for cut in cut_times)
            pending_cut = pending_cut or scene_cut
            face_jump = (
                face is not None
                and prev_face_cx is not None
                and abs(face.cx - prev_face_cx) > framing.JUMP_CUT * source_w
            )
            return_after_loss = face is not None and (
                (last_face_t is None and crop.t > framing.LOSS_S)
                or (last_face_t is not None and crop.t - last_face_t > framing.LOSS_S)
            )
            if dt > 0 and not (pending_cut or face_jump or return_after_loss):
                worst = max(worst, abs(crop.x - prev.x) / dt)
        if face is not None:
            pending_cut = False
            last_face_t = crop.t
            prev_face_cx = face.cx
    return round(worst, 1)


def _hold_spans(
    track: list[subject.TrackSample | None],
    sample_times: list[float],
) -> int:
    """Count runs of consecutive faceless samples longer than LOSS_S."""
    count = 0
    run_start: int | None = None
    for i, sample in enumerate(track):
        if sample is None:
            if run_start is None:
                run_start = i
        else:
            if run_start is not None:
                duration = sample_times[i - 1] - sample_times[run_start]
                if duration > framing.LOSS_S:
                    count += 1
                run_start = None
    if run_start is not None:
        duration = sample_times[len(track) - 1] - sample_times[run_start]
        if duration > framing.LOSS_S:
            count += 1
    return count


def _pan_cap_ok(governed_pan: float, source_w: int) -> bool:
    return governed_pan <= framing.PAN_CAP * source_w + 2 * framing.SAMPLE_FPS


def _process_one(
    source: Path, info: MediaInfo, out_dir: Path, profile: str = "speech"
) -> dict:
    """Run detection, framing, and sheet for one clip. Return the report row."""
    t0 = time.monotonic()

    if info.field_order not in {"progressive", "unknown"}:
        raise RuntimeError("interlaced input is not safe for subject detection")
    timeout = subject.analysis_timeout(info.duration)
    deadline = time.monotonic() + timeout
    track, sample_times = subject.detect_track_owned(source, info, timeout)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("deadline passed before scene detection")
    cuts = subject.scene_cuts(source, remaining)
    plan = framing.plan_crop(
        track,
        cuts,
        info.width,
        info.height,
        sample_times=sample_times,
        profile=profile,
    )
    elapsed = round(time.monotonic() - t0, 2)

    sheet = _build_contact_sheet(source, track, plan, info)
    sheet_path = out_dir / f"{source.stem}.png"
    cv2, _np = _cv_modules()
    if not cv2.imwrite(str(sheet_path), sheet):
        raise RuntimeError("could not write contact sheet")

    governed_pan = _max_governed_pan_px_per_s(plan, track, cuts, info.width)

    return {
        "name": source.name,
        "width": info.width,
        "height": info.height,
        "duration": round(info.duration, 2),
        "decision": plan.decision,
        "reason": plan.reason,
        "face_rate": round(plan.face_rate, 3),
        "safe_rate": round(plan.safe_rate, 3),
        "warning": plan.warning,
        "max_pan_px_per_s": _max_pan_px_per_s(plan),
        "max_governed_pan_px_per_s": governed_pan,
        "pan_cap_ok": _pan_cap_ok(governed_pan, info.width),
        "cuts": len(cuts),
        "hold_spans": _hold_spans(track, sample_times),
        "analysis_s": elapsed,
    }


def _passes(role: str, row: dict) -> bool:
    """True when the clip meets its expected outcome."""
    common = row["analysis_s"] <= ANALYSIS_TARGET_S and row["pan_cap_ok"]
    if role == "no_face":
        return common and row["decision"] == "blur_pad"
    return (
        common
        and row["decision"] == "crop"
        and row["face_rate"] >= framing.FACE_RATE_MIN
        and row["safe_rate"] >= framing.SAFE_RATE_MIN
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Contact sheet and report for landscape fixture clips."
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default="fixtures/landscape",
        help="Directory with .mp4/.mov clips (default: fixtures/landscape).",
    )
    parser.add_argument(
        "--contact-sheets-approved",
        action="store_true",
        help=(
            "Attest that all six sheets passed manual visual review, including "
            "two-shot identity continuity."
        ),
    )
    parser.add_argument(
        "--out",
        default=None,
        help="Output directory (default: <directory>/out).",
    )
    parser.add_argument(
        "--profile",
        choices=["speech", "music"],
        default="speech",
        help="Content profile label for report rows (default: speech).",
    )
    parser.add_argument(
        "--roles",
        choices=["check", "none"],
        default="check",
        help="Role validation: check (default) or none to skip.",
    )
    args = parser.parse_args(argv)

    clip_dir = Path(args.directory)
    if not clip_dir.is_dir():
        print(f"ERROR: {clip_dir} is not a directory.", file=sys.stderr)
        return 1

    out_dir = Path(args.out) if args.out else clip_dir / "out"
    out_dir.mkdir(parents=True, exist_ok=True)

    clips = sorted(p for p in clip_dir.iterdir() if p.suffix.lower() in SUFFIXES)
    if not clips:
        print(f"No .mp4/.mov files in {clip_dir}.", file=sys.stderr)
        return 1
    roles: dict[Path, str] = {}
    if args.roles == "check":
        try:
            roles = _fixture_roles(clips)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    report: list[dict] = []
    passed = 0
    total = 0

    header = f"{'name':<30} {'WxH':>10} {'dur':>6} {'decision':>10} {'reason':>18} {'face%':>6} {'safe%':>6} {'warn':>14} {'pan':>8} {'cuts':>5} {'t':>6} {'pass':>5}"
    print(header)
    print("-" * len(header))

    for clip in clips:
        role = roles.get(clip)
        total += 1
        try:
            info = probe(str(clip))
        except Exception as exc:
            print(f"{clip.name:<30} PROBE ERROR: {exc}")
            report.append({"name": clip.name, "role": role, "error": str(exc)})
            continue

        if not _is_landscape(info):
            print(
                f"{clip.name:<30} skipped (not landscape: {info.width}x{info.height})"
            )
            report.append({"name": clip.name, "role": role, "error": "not landscape"})
            continue

        try:
            row = _process_one(clip, info, out_dir, profile=args.profile)
        except Exception as exc:
            print(f"{clip.name:<30} FAILED: {exc}")
            report.append({"name": clip.name, "role": role, "error": str(exc)})
            continue

        row["role"] = role
        row["profile"] = args.profile
        report.append(row)
        ok = None
        if role is not None:
            ok = _passes(role, row)
            if ok:
                passed += 1

        gate_col = "-" if ok is None else ("PASS" if ok else "FAIL")
        print(
            f"{row['name']:<30} "
            f"{row['width']}x{row['height']:>4} "
            f"{row['duration']:>6.1f} "
            f"{row['decision']:>10} "
            f"{row['reason']:>18} "
            f"{row['face_rate']:>5.1%} "
            f"{row['safe_rate']:>5.1%} "
            f"{row['warning'] or ''!s:>14} "
            f"{row['max_pan_px_per_s']:>7.1f} "
            f"{row['cuts']:>5} "
            f"{row['analysis_s']:>5.1f}s "
            f"{gate_col:>5}"
        )

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nprocessed {total} clips")
    if args.roles == "check":
        print(f"passed {passed} of {total}")
    print(f"Report: {report_path}")
    print(f"Sheets: {out_dir}/*.png")

    if args.roles == "none":
        return 0

    if not args.contact_sheets_approved:
        print(
            "Contact-sheet approval is required; inspect all six sheets and rerun "
            "with --contact-sheets-approved only if they pass visual review.",
            file=sys.stderr,
        )

    return 0 if passed >= 5 and total == 6 and args.contact_sheets_approved else 1


if __name__ == "__main__":
    raise SystemExit(main())
