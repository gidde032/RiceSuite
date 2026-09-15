"""Unit tests for render.subject: detection, scene cuts, and build_plan.

detect_track is driven with a fake capture and fake detector, so these tests
need no cv2 and shell out to nothing. One real-cv2 smoke test loads the vendored
model and is skipped when OpenCV is not installed.
"""

from __future__ import annotations

import importlib.util
import time

import pytest

from app.models import CropSample
from app.probe import MediaInfo
from app.process import ProcessTimeoutError
from render import subject

_LANDSCAPE = MediaInfo(width=640, height=360, duration=4.0, has_audio=True)


class FakeCapture:
    """Stand-in for cv2.VideoCapture. Each frame is the detection to return."""

    def __init__(self, frames, fps):
        self._frames = list(frames)
        self._fps = fps
        self.released = False

    def get(self, _prop):
        return float(self._fps)

    def read(self):
        if self._frames:
            return True, self._frames.pop(0)
        return False, None

    def release(self):
        self.released = True


class Frame:
    """A decoded frame carrying the face rows the fake detector returns."""

    def __init__(self, faces):
        self.faces = faces


class FakeDetector:
    """Returns each frame's preset face rows (a list of rows, or None)."""

    def detect(self, frame):
        return frame.faces


def _track(faces_per_frame, fps, info=_LANDSCAPE, **kwargs):
    frames = [Frame(faces) for faces in faces_per_frame]
    capture = FakeCapture(frames, fps)
    return (
        subject.detect_track(
            "source.mp4",
            info,
            capture_factory=lambda _src: capture,
            detector_factory=FakeDetector,
            **kwargs,
        ),
        capture,
    )


def test_detect_track_samples_one_per_step():
    # 30 fps -> step = 6. Frames 0..6: only indices 0 and 6 are sampled.
    face = [[300, 300, 40, 40, 0.9]]
    frames = [face, None, None, None, None, None, face]
    samples, capture = _track(frames, fps=30)

    assert len(samples) == 2
    assert samples[0].t == pytest.approx(0.0)
    assert samples[1].t == pytest.approx(6 / 30)
    assert samples[0].cx == pytest.approx(320.0)
    assert capture.released is True


def test_detect_track_keeps_largest_box():
    small = [10, 10, 20, 20, 0.9]
    large = [400, 100, 80, 80, 0.9]
    samples, _ = _track([[small, large]], fps=5)

    assert len(samples) == 1
    # Large box center: 400 + 80/2 = 440.
    assert samples[0].cx == pytest.approx(440.0)


def test_detect_track_continuity_prefers_near_box_over_larger():
    frame0 = [[480, 100, 40, 40, 0.9]]  # cx = 500, establishes the target
    near = [540, 100, 20, 20, 0.9]  # cx = 550, dist 50 < 0.15*640 = 96
    far_large = [80, 100, 100, 100, 0.9]  # cx = 130, larger but far
    samples, _ = _track([frame0, [near, far_large]], fps=5)

    assert len(samples) == 2
    assert samples[0].cx == pytest.approx(500.0)
    assert samples[1].cx == pytest.approx(550.0)


def test_detect_track_none_on_no_box_or_low_score():
    low = [[300, 300, 40, 40, 0.4]]  # below SCORE_MIN
    samples, _ = _track([None, low], fps=5)

    assert samples == [None, None]


def test_detect_track_deadline_raises():
    frames = [[[300, 300, 40, 40, 0.9]]]
    with pytest.raises(TimeoutError):
        _track(frames, fps=5, deadline=time.monotonic() - 1.0)


def test_scene_cuts_parses_pts_time(monkeypatch):
    class _Result:
        stderr = (
            "[Parsed_showinfo_1 @ 0x1] n:0 pts:60 pts_time:2.5 duration:1\n"
            "[Parsed_showinfo_1 @ 0x1] n:1 pts:24 pts_time:1.0 duration:1\n"
        )

    monkeypatch.setattr(subject, "run_owned", lambda *a, **k: _Result())
    assert subject.scene_cuts("source.mp4", timeout=10.0) == [1.0, 2.5]


def test_build_plan_analysis_failed_when_detection_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(subject, "detect_track", boom)
    plan = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert plan.decision == "blur_pad"
    assert plan.reason == "analysis_failed"


def test_build_plan_analysis_failed_when_scene_cuts_times_out(monkeypatch):
    monkeypatch.setattr(subject, "detect_track", lambda *a, **k: [])

    def timeout(*a, **k):
        raise ProcessTimeoutError(["ffmpeg"], 1.0)

    monkeypatch.setattr(subject, "scene_cuts", timeout)
    plan = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert plan.decision == "blur_pad"
    assert plan.reason == "analysis_failed"


def test_build_plan_analysis_failed_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(subject, "MODEL_PATH", tmp_path / "missing.onnx")
    plan = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert plan.decision == "blur_pad"
    assert plan.reason == "analysis_failed"


def test_analysis_timeout_floor_and_scaling():
    assert subject.analysis_timeout(10.0) == 60.0
    assert subject.analysis_timeout(100.0) == 200.0


@pytest.mark.skipif(
    importlib.util.find_spec("cv2") is None, reason="OpenCV not installed"
)
def test_yunet_model_loads_with_real_cv2():
    import cv2

    detector = cv2.FaceDetectorYN.create(
        str(subject.MODEL_PATH),
        "",
        (subject.DETECT_WIDTH, subject.DETECT_WIDTH),
        subject.SCORE_MIN,
    )
    assert detector is not None
    assert subject.MODEL_PATH.exists()


def test_crop_sample_is_even_int_contract():
    # Guards the CropSample x type the plan writes into crop.cmd (geometry F2).
    sample = CropSample(t=0.2, x=100)
    assert sample.x == 100
