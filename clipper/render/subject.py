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

import json
import logging
import math
import multiprocessing
import re
import tempfile
import time
from pathlib import Path

from app.models import CropPlan, TrackSample
from app.probe import MediaInfo
from app.process import run_owned
from render import framing

logger = logging.getLogger("riceclipper")

MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"
DETECT_WIDTH = 640
SCORE_MIN = 0.5
CONTINUITY = 0.15
SCENE_MIN = 0.2

# Effective sampling rate. Decoded presentation timestamps are retained for
# every sampled frame, including samples without a face.
SAMPLE_FPS = 5

# OpenCV VideoCaptureProperties enum values, stable across releases. Named here
# so the fake-capture unit-test path never has to import cv2.
_CAP_PROP_FPS = 5
_CAP_PROP_FRAME_COUNT = 7
_CAP_PROP_POS_MSEC = 0
_CAP_PROP_ORIENTATION_AUTO = 49

_PTS_TIME = re.compile(r"Parsed_showinfo.*pts_time:([0-9]+\.?[0-9]*)")
_SCENE_SCORE = re.compile(r"lavfi\.scene_score=([0-9]+\.?[0-9]*)")


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
    faces,
    info: MediaInfo,
    prev_cx: float | None,
    scale: float,
    *,
    after_cut: bool = False,
) -> tuple[float, float, float, float] | None:
    """Pick one face box from a detection, in source pixels, or ``None``.

    After a scene cut, return the largest box. Between cuts, prefer the box
    nearest ``prev_cx`` inside ``CONTINUITY``; else the largest.
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

    if after_cut:
        return max(candidates, key=area)
    if prev_cx is not None:
        near = [c for c in candidates if abs(c[0] - prev_cx) <= CONTINUITY * info.width]
        if near:
            return min(near, key=lambda c: abs(c[0] - prev_cx))
    return max(candidates, key=area)


def detect_track(
    source: Path,
    info: MediaInfo,
    *,
    capture_factory=None,
    detector_factory=None,
    deadline: float | None = None,
    sample_times: list[float] | None = None,
    cuts: list[tuple[float, float]] | None = None,
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
    times = sample_times if sample_times is not None else []
    cut_times = cuts or []
    prev_cx: float | None = None
    prev_t: float | None = None
    try:
        if info.rotation:
            set_property = getattr(capture, "set", None)
            if not callable(set_property) or not set_property(
                _CAP_PROP_ORIENTATION_AUTO, 1
            ):
                raise RuntimeError("video backend cannot apply display rotation")
        fps = _capture_fps(capture, info)
        step = max(1, round(fps / SAMPLE_FPS))
        try:
            expected_frames = float(capture.get(_CAP_PROP_FRAME_COUNT))
        except Exception:
            expected_frames = 0.0
        frame_index = 0
        last_decoded_t = 0.0
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise TimeoutError("subject detection exceeded its deadline")
            ok, frame = capture.read()
            if not ok or frame is None:
                break
            try:
                reported_t = float(capture.get(_CAP_PROP_POS_MSEC)) / 1000.0
            except Exception:
                reported_t = float("nan")
            fallback_t = frame_index / fps
            if not math.isfinite(reported_t) or reported_t < 0:
                reported_t = fallback_t
            if frame_index and reported_t <= last_decoded_t:
                reported_t = max(fallback_t, last_decoded_t + 1.0 / fps)
            last_decoded_t = reported_t
            if frame_index % step == 0:
                shape = getattr(frame, "shape", None)
                if shape is not None and (shape[1], shape[0]) != (
                    info.width,
                    info.height,
                ):
                    import cv2

                    frame = cv2.resize(frame, (info.width, info.height))
                is_after_cut = prev_t is not None and any(
                    prev_t < ct <= reported_t
                    for ct, sc in cut_times
                    if sc > framing.SCENE_MIN_SPEECH
                )
                box = _select_box(
                    detector.detect(frame),
                    info,
                    prev_cx,
                    scale,
                    after_cut=is_after_cut,
                )
                times.append(reported_t)
                if box is None:
                    samples.append(None)
                else:
                    cx, cy, w, h = box
                    samples.append(TrackSample(t=reported_t, cx=cx, cy=cy, w=w, h=h))
                    prev_cx = cx
                prev_t = reported_t
            frame_index += 1
        if frame_index == 0:
            raise RuntimeError("video decode produced no frames")
        tolerance_frames = max(step * 2, round(fps))
        has_expected_frames = math.isfinite(expected_frames) and expected_frames > 0
        if has_expected_frames and frame_index + tolerance_frames < expected_frames:
            raise RuntimeError("video decode ended early")
        decoded_end = last_decoded_t + 1.0 / fps
        if decoded_end + 1.0 < info.duration:
            raise RuntimeError("video decode ended early")
    finally:
        release = getattr(capture, "release", None)
        if callable(release):
            release()
    return samples


def _detect_worker(
    result_path: str,
    source: str,
    info: MediaInfo,
    cuts: list[tuple[float, float]] | None = None,
) -> None:
    """Run native OpenCV work in a killable child process."""
    try:
        sample_times: list[float] = []
        track = detect_track(Path(source), info, sample_times=sample_times, cuts=cuts)
        payload = [
            sample.model_dump() if sample is not None else None for sample in track
        ]
        result = {"ok": True, "track": payload, "sample_times": sample_times}
    except BaseException as exc:
        result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    Path(result_path).write_text(json.dumps(result), encoding="utf-8")


def _process_context():
    return multiprocessing.get_context("spawn")


def detect_track_owned(
    source: Path,
    info: MediaInfo,
    timeout: float,
    cuts: list[tuple[float, float]] | None = None,
) -> tuple[list[TrackSample | None], list[float]]:
    """Run detection in a subprocess that can be terminated at the hard deadline."""
    context = _process_context()
    result_file = tempfile.NamedTemporaryFile(
        prefix="riceclipper-detect-", suffix=".json", delete=False
    )
    result_path = Path(result_file.name)
    result_file.close()
    process = None
    try:
        process = context.Process(
            target=_detect_worker,
            args=(str(result_path), str(source), info, cuts),
            daemon=True,
        )
        process.start()
        process.join(timeout)
        if process is not None and process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive():
                process.kill()
                process.join(1.0)
            raise TimeoutError("subject detection exceeded its deadline")
        if process.exitcode != 0:
            raise RuntimeError(
                f"subject detection process exited with code {process.exitcode}"
            )
        if result_path.stat().st_size > 5_000_000:
            raise RuntimeError("subject detection result exceeded its size limit")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        if not result.get("ok"):
            raise RuntimeError(
                f"subject detection failed: {result.get('error', 'unknown')}"
            )
        payload = result["track"]
        sample_times = result["sample_times"]
        track = [
            TrackSample(**sample) if sample is not None else None for sample in payload
        ]
        return track, sample_times
    finally:
        if process.is_alive():
            process.terminate()
            process.join(1.0)
            if process.is_alive():
                process.kill()
                process.join(1.0)
        result_path.unlink(missing_ok=True)


def scene_cuts(source: Path, timeout: float) -> list[tuple[float, float]]:
    """Return scene cuts as ``(t, score)`` sorted by time via one ffmpeg pass."""
    result = run_owned(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-vf",
            f"select='gt(scene,{SCENE_MIN})',metadata=print:key=lavfi.scene_score,showinfo",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError("scene detection failed")
    stderr = result.stderr or ""
    scores = [float(m) for m in _SCENE_SCORE.findall(stderr)]
    times = [float(m) for m in _PTS_TIME.findall(stderr)]
    pairs = list(zip(times, scores, strict=True))
    pairs.sort(key=lambda pair: pair[0])
    return pairs


def build_plan(source: Path, info: MediaInfo) -> tuple[CropPlan, CropPlan]:
    """Analyse ``source`` and return ``(speech_plan, music_plan)``.

    Rides one deadline over scene cuts and detection. Catches every failure,
    logs it, and returns analysis_failed plans for both profiles.
    """
    timeout = analysis_timeout(info.duration)
    deadline = time.monotonic() + timeout
    try:
        if info.field_order not in {"progressive", "unknown"}:
            raise RuntimeError("interlaced input is not safe for subject detection")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("analysis deadline passed before scene detection")
        cuts = scene_cuts(source, remaining)
        if time.monotonic() > deadline:
            raise TimeoutError("scene detection exceeded the analysis deadline")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("analysis deadline passed before detection")
        track, sample_times = detect_track_owned(source, info, remaining, cuts=cuts)
        speech = framing.plan_crop(
            track,
            cuts,
            info.width,
            info.height,
            sample_times=sample_times,
            profile="speech",
        )
        music = framing.plan_crop(
            track,
            cuts,
            info.width,
            info.height,
            sample_times=sample_times,
            profile="music",
        )
        return speech, music
    except Exception:
        logger.exception("subject-crop analysis failed for %s", source)
        return (
            framing.failed_plan(
                "analysis_failed", info.width, info.height, profile="speech"
            ),
            framing.failed_plan(
                "analysis_failed", info.width, info.height, profile="music"
            ),
        )
