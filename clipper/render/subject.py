"""YuNet detection and scene cuts for the subject-focused 9:16 crop (ADR-001).

This module produces the inputs that :mod:`render.framing` turns into a
:class:`~app.models.CropPlan`:

* :func:`detect_track` decodes the source with OpenCV, samples ~5 frames per
  second, runs the vendored YuNet detector, and returns one :class:`TrackSample`
  (or ``None``) per sample in source pixels.
* :func:`scene_cuts` runs one ffmpeg pass and returns scene-cut times.
* :func:`build_plan` rides one analysis deadline over both, then calls
  ``framing.plan_crop``. It never raises: any failure yields a blur-pad plan
  with reason ``analysis_failed`` (SPEC hard rule: analysis never fails the job).

``cv2`` is imported lazily, only when a real detector or capture is built, so the
unit tests drive :func:`detect_track` with fakes and no OpenCV dependency.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from app.models import CropPlan, TrackSample
from app.probe import MediaInfo
from app.process import run_owned
from render import framing

logger = logging.getLogger("riceclipper")

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_WIDTH = 640
SCORE_MIN = 0.7
CONTINUITY = 0.15
SCENE_MIN = 0.4

# Effective sampling rate. framing fills a lost sample's time as ``i / 5``, so
# the sample index must track ~5 samples per second.
SAMPLE_FPS = 5

# OpenCV VideoCaptureProperties enum values, stable across releases. Named here
# so the fake-capture unit-test path never has to import cv2.
_CAP_PROP_FPS = 5
_CAP_PROP_FRAME_COUNT = 7

_PTS_TIME = re.compile(r"pts_time:([0-9]+\.?[0-9]*)")


def analysis_timeout(duration: float) -> float:
    """Return the wall-clock budget for one clip's crop analysis."""
    return max(60.0, duration * 2)


def _make_yunet_detector():
    """Build a YuNet detector that maps frames to source-pixel face boxes.

    Imported lazily: cv2 loads only when a real detector is needed.
    """
    import cv2

    net = cv2.FaceDetectorYN.create(
        str(MODEL_PATH), "", (DETECT_WIDTH, DETECT_WIDTH), SCORE_MIN
    )

    class _Detector:
        def detect(self, frame):
            height, width = frame.shape[:2]
            scale = DETECT_WIDTH / width
            resized = cv2.resize(frame, (DETECT_WIDTH, max(1, round(height * scale))))
            net.setInputSize((resized.shape[1], resized.shape[0]))
            _, faces = net.detect(resized)
            return faces

    return _Detector()


def _capture_fps(capture, info: MediaInfo) -> float:
    """Return the capture frame rate, falling back to duration then SAMPLE_FPS."""
    try:
        fps = float(capture.get(_CAP_PROP_FPS))
    except Exception:
        fps = 0.0
    if fps > 0:
        return fps
    try:
        frames = float(capture.get(_CAP_PROP_FRAME_COUNT))
    except Exception:
        frames = 0.0
    if frames > 0 and info.duration > 0:
        return frames / info.duration
    # Last resort: sample every frame at the target rate; t becomes i / 5.
    return float(SAMPLE_FPS)


def _select_box(
    faces, info: MediaInfo, prev_cx: float | None, scale: float
) -> tuple[float, float, float, float] | None:
    """Pick one face box from a detection, in source pixels, or ``None``.

    Rules (subject-crop-spec Detection): drop boxes below ``SCORE_MIN``; keep the
    largest by area; but if a previous target exists, prefer the largest box
    whose center is within ``CONTINUITY`` of source width of it.
    """
    if faces is None:
        return None
    candidates: list[tuple[float, float, float, float]] = []
    for row in faces:
        if float(row[-1]) < SCORE_MIN:
            continue
        w = float(row[2]) * scale
        h = float(row[3]) * scale
        if w <= 0 or h <= 0:
            continue
        cx = float(row[0]) * scale + w / 2
        cy = float(row[1]) * scale + h / 2
        candidates.append((cx, cy, w, h))
    if not candidates:
        return None

    def area(box: tuple[float, float, float, float]) -> float:
        return box[2] * box[3]

    if prev_cx is not None:
        near = [c for c in candidates if abs(c[0] - prev_cx) <= CONTINUITY * info.width]
        if near:
            return max(near, key=area)
    return max(candidates, key=area)


def detect_track(
    source: Path,
    info: MediaInfo,
    *,
    capture_factory=None,
    detector_factory=None,
    deadline: float | None = None,
) -> list[TrackSample | None]:
    """Sample the source and return one face box (or ``None``) per sample.

    Samples every ``max(1, round(fps / 5))``-th frame. Each sampled frame yields
    a :class:`TrackSample` in source pixels, or ``None`` when no face passes the
    score, area, and continuity rules. Raises :class:`TimeoutError` if the
    ``deadline`` (a ``time.monotonic`` value) passes mid-decode.
    """
    if capture_factory is None or detector_factory is None:
        import cv2

        if capture_factory is None:
            capture_factory = cv2.VideoCapture
        if detector_factory is None:
            detector_factory = _make_yunet_detector

    capture = capture_factory(str(source))
    detector = detector_factory()
    scale = info.width / DETECT_WIDTH
    samples: list[TrackSample | None] = []
    prev_cx: float | None = None
    try:
        fps = _capture_fps(capture, info)
        step = max(1, round(fps / SAMPLE_FPS))
        frame_index = 0
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("subject detection exceeded its deadline")
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            if frame_index % step == 0:
                box = _select_box(detector.detect(frame), info, prev_cx, scale)
                if box is None:
                    samples.append(None)
                else:
                    cx, cy, w, h = box
                    samples.append(
                        TrackSample(t=frame_index / fps, cx=cx, cy=cy, w=w, h=h)
                    )
                    prev_cx = cx
            frame_index += 1
    finally:
        release = getattr(capture, "release", None)
        if callable(release):
            release()
    return samples


def scene_cuts(source: Path, timeout: float) -> list[float]:
    """Return sorted scene-cut times (seconds) via one ffmpeg showinfo pass."""
    result = run_owned(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-vf",
            f"select='gt(scene,{SCENE_MIN})',showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    stderr = result.stderr or ""
    return sorted(float(m) for m in _PTS_TIME.findall(stderr))


def build_plan(source: Path, info: MediaInfo) -> CropPlan:
    """Analyse ``source`` and return a :class:`~app.models.CropPlan`.

    Rides one deadline over detection and scene cuts. Catches every failure,
    logs it, and returns a blur-pad plan with reason ``analysis_failed``. It
    never raises and never fails the owning job.
    """
    timeout = analysis_timeout(info.duration)
    deadline = time.monotonic() + timeout
    try:
        track = detect_track(source, info, deadline=deadline)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("analysis deadline passed before scene detection")
        cuts = scene_cuts(source, remaining)
        return framing.plan_crop(track, cuts, info.width, info.height)
    except Exception:
        logger.exception("subject-crop analysis failed for %s", source)
        return framing.failed_plan("analysis_failed", info.width, info.height)
