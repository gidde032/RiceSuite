#!/usr/bin/env python3
"""Contact-sheet and report generator for the subject-crop fixture set.

For each landscape clip in a directory: probe, detect, frame, then write a
12-frame contact sheet with the crop window (green) and nearest face box (red),
plus a JSON report row.  Not under coverage; not in CI.  Maintainer runs it.

Usage:
    python scripts/crop_check.py [fixtures/landscape] [--out fixtures/landscape/out]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Ensure project root is importable.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.probe import MediaInfo, probe  # noqa: E402
from render import framing, subject  # noqa: E402

SUFFIXES = {".mp4", ".mov"}
GRID_COLS = 4
GRID_ROWS = 3
THUMB_W = 480
GREEN = (0, 255, 0)
RED = (0, 0, 255)
THICKNESS = 2


def _is_landscape(info: MediaInfo) -> bool:
    return info.width > info.height


def _grab_frames(source: Path, count: int) -> list[tuple[float, np.ndarray]]:
    """Return up to *count* frames at equal time steps."""
    cap = cv2.VideoCapture(str(source))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if total <= 0:
            return []
        step = max(1, total // count)
        frames: list[tuple[float, np.ndarray]] = []
        for i in range(count):
            idx = i * step
            cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
            ok, frame = cap.read()
            if not ok or frame is None:
                break
            t = idx / fps
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
    frame: np.ndarray,
    t: float,
    track: list[subject.TrackSample | None],
    plan: framing.CropPlan,
    src_w: int,
    src_h: int,
) -> np.ndarray:
    """Draw crop window (green) and nearest face box (red) on a copy."""
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
) -> np.ndarray:
    """Build a 4x3 contact sheet at THUMB_W per cell."""
    count = GRID_COLS * GRID_ROWS
    frames = _grab_frames(source, count)
    if not frames:
        return np.zeros((100, 100, 3), dtype=np.uint8)

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


def _process_one(source: Path, info: MediaInfo, out_dir: Path) -> dict:
    """Run detection, framing, and sheet for one clip. Return the report row."""
    t0 = time.monotonic()

    timeout = subject.analysis_timeout(info.duration)
    deadline = time.monotonic() + timeout
    track = subject.detect_track(source, info, deadline=deadline)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("deadline passed before scene detection")
    cuts = subject.scene_cuts(source, remaining)
    plan = framing.plan_crop(track, cuts, info.width, info.height)

    sheet = _build_contact_sheet(source, track, plan, info)
    sheet_path = out_dir / f"{source.stem}.png"
    cv2.imwrite(str(sheet_path), sheet)

    elapsed = round(time.monotonic() - t0, 2)

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
        "cuts": len(cuts),
        "analysis_s": elapsed,
    }


def _passes(name: str, row: dict) -> bool:
    """True when the clip meets its expected outcome."""
    if "noface" in name.lower():
        return row["decision"] == "blur_pad"
    return row["decision"] == "crop" and row["safe_rate"] >= 0.95


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
        "--out",
        default=None,
        help="Output directory (default: <directory>/out).",
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

    report: list[dict] = []
    passed = 0
    total = 0

    header = f"{'name':<30} {'WxH':>10} {'dur':>6} {'decision':>10} {'reason':>18} {'face%':>6} {'safe%':>6} {'warn':>14} {'pan':>8} {'cuts':>5} {'t':>6} {'pass':>5}"
    print(header)
    print("-" * len(header))

    for clip in clips:
        try:
            info = probe(str(clip))
        except Exception as exc:
            print(f"{clip.name:<30} PROBE ERROR: {exc}")
            continue

        if not _is_landscape(info):
            print(
                f"{clip.name:<30} skipped (not landscape: {info.width}x{info.height})"
            )
            continue

        total += 1
        try:
            row = _process_one(clip, info, out_dir)
        except Exception as exc:
            print(f"{clip.name:<30} FAILED: {exc}")
            continue

        report.append(row)
        ok = _passes(clip.name, row)
        if ok:
            passed += 1

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
            f"{'PASS' if ok else 'FAIL':>5}"
        )

    report_path = out_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\npassed {passed} of {total}")
    print(f"Report: {report_path}")
    print(f"Sheets: {out_dir}/*.png")

    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
