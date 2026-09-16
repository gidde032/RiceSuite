"""Unit tests for render.subject: detection, scene cuts, and build_plan.

detect_track is driven with a fake capture and fake detector, so these tests
need no cv2 and shell out to nothing. One real-cv2 smoke test loads the vendored
model and is required by the installed runtime dependencies.
"""

from __future__ import annotations

import time

import pytest

from app.models import CropSample
from app.probe import MediaInfo
from app.process import ProcessTimeoutError
from render import subject

_LANDSCAPE = MediaInfo(width=640, height=360, duration=4.0, has_audio=True)


class FakeCapture:
    """Stand-in for cv2.VideoCapture. Each frame is the detection to return."""

    def __init__(self, frames, fps, *, expected_frames=None, pts_ms=None):
        self._frames = list(frames)
        self._fps = fps
        self._expected_frames = (
            expected_frames if expected_frames is not None else len(frames)
        )
        self._pts_ms = list(pts_ms or [])
        self._reads = 0
        self.released = False

    def get(self, prop):
        if prop == subject._CAP_PROP_FPS:
            return float(self._fps)
        if prop == subject._CAP_PROP_FRAME_COUNT:
            return float(self._expected_frames)
        if prop == subject._CAP_PROP_POS_MSEC and self._reads:
            if self._reads - 1 < len(self._pts_ms):
                return float(self._pts_ms[self._reads - 1])
        return 0.0

    def read(self):
        if self._frames:
            self._reads += 1
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


def _track(faces_per_frame, fps, info=None, **kwargs):
    frames = [Frame(faces) for faces in faces_per_frame]
    if info is None:
        info = MediaInfo(640, 360, len(frames) / fps, True)
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


def test_select_box_after_cut_picks_largest():
    info = MediaInfo(640, 360, 4.0, True)
    near_small = [480, 100, 20, 20, 0.9]  # cx = 490, near prev_cx 500
    far_large = [80, 100, 100, 100, 0.9]  # cx = 130, larger
    box = subject._select_box(
        [near_small, far_large], info, prev_cx=500.0, scale=1.0, after_cut=True
    )
    assert box is not None
    assert box[0] == pytest.approx(130.0)


def test_select_box_between_cuts_picks_nearest():
    info = MediaInfo(640, 360, 4.0, True)
    near = [480, 100, 30, 30, 0.9]  # cx = 495
    far_large = [80, 100, 100, 100, 0.9]  # cx = 130
    box = subject._select_box(
        [near, far_large], info, prev_cx=500.0, scale=1.0, after_cut=False
    )
    assert box is not None
    assert box[0] == pytest.approx(495.0)


def test_select_box_no_near_falls_back_to_largest():
    info = MediaInfo(640, 360, 4.0, True)
    far_a = [10, 100, 40, 40, 0.9]  # cx = 30, dist > 0.15*640 = 96
    far_b = [100, 100, 80, 80, 0.9]  # cx = 140, larger, also far
    box = subject._select_box(
        [far_a, far_b], info, prev_cx=500.0, scale=1.0, after_cut=False
    )
    assert box is not None
    assert box[0] == pytest.approx(140.0)


def test_detect_track_none_on_no_box_or_low_score():
    low = [[300, 300, 40, 40, 0.4]]  # below SCORE_MIN
    samples, _ = _track([None, low], fps=5)

    assert samples == [None, None]


def test_detect_track_deadline_raises():
    frames = [[[300, 300, 40, 40, 0.9]]]
    with pytest.raises(TimeoutError):
        _track(frames, fps=5, deadline=time.monotonic() - 1.0)


def test_scene_cuts_parses_pts_time_and_score(monkeypatch):
    class _Result:
        returncode = 0
        stderr = (
            "[Parsed_metadata_1 @ 0x1] lavfi.scene_score=0.650000\n"
            "[Parsed_showinfo_1 @ 0x1] n:0 pts:60 pts_time:2.5 duration:1\n"
            "[Parsed_metadata_1 @ 0x1] lavfi.scene_score=0.250000\n"
            "[Parsed_showinfo_1 @ 0x1] n:1 pts:24 pts_time:1.0 duration:1\n"
        )

    monkeypatch.setattr(subject, "run_owned", lambda *a, **k: _Result())
    result = subject.scene_cuts("source.mp4", timeout=10.0)
    assert result == [(1.0, 0.25), (2.5, 0.65)]


def test_scene_cuts_rejects_nonzero_ffmpeg_exit(monkeypatch):
    class _Result:
        returncode = 1
        stderr = "decoder error after pts_time:1.0"

    monkeypatch.setattr(subject, "run_owned", lambda *a, **k: _Result())

    with pytest.raises(RuntimeError, match="scene detection failed"):
        subject.scene_cuts("source.mp4", timeout=10.0)


def test_detect_track_rejects_premature_decode_eof():
    face = [[300, 300, 40, 40, 0.9]]
    capture = FakeCapture([Frame(face)] * 5, fps=5, expected_frames=50)

    with pytest.raises(RuntimeError, match="decode ended early"):
        subject.detect_track(
            "source.mp4",
            MediaInfo(640, 360, 10.0, True),
            capture_factory=lambda _src: capture,
            detector_factory=FakeDetector,
        )


@pytest.mark.parametrize("expected_frames", [1, float("nan")])
def test_detect_track_rejects_short_decode_despite_bad_frame_count(expected_frames):
    face = [[300, 300, 40, 40, 0.9]]
    capture = FakeCapture([Frame(face)], fps=5, expected_frames=expected_frames)

    with pytest.raises(RuntimeError, match="decode ended early"):
        subject.detect_track(
            "source.mp4",
            MediaInfo(640, 360, 60.0, True),
            capture_factory=lambda _src: capture,
            detector_factory=FakeDetector,
        )


def test_detect_track_records_pts_for_missing_samples():
    face = [[300, 300, 40, 40, 0.9]]
    capture = FakeCapture(
        [Frame(face), Frame(None), Frame(face)],
        fps=5,
        pts_ms=[0.0, 150.0, 1000.0],
    )
    sample_times = []

    samples = subject.detect_track(
        "source.mp4",
        MediaInfo(640, 360, 1.2, True),
        capture_factory=lambda _src: capture,
        detector_factory=FakeDetector,
        sample_times=sample_times,
    )

    assert samples[1] is None
    assert sample_times == [0.0, 0.15, 1.0]


def test_detect_track_owned_enforces_hard_timeout(monkeypatch):
    class FakeProcess:
        def __init__(self):
            self.alive = False
            self.terminated = False

        def start(self):
            self.alive = True

        def terminate(self):
            self.terminated = True
            self.alive = False

        def join(self, timeout=None):
            assert timeout in {0.01, 1.0}

        def is_alive(self):
            return self.alive

        def kill(self):
            self.alive = False

    process = FakeProcess()

    class FakeContext:
        def Process(self, **kwargs):
            assert kwargs["target"] is subject._detect_worker
            return process

    monkeypatch.setattr(subject, "_process_context", lambda: FakeContext())

    with pytest.raises(TimeoutError, match="exceeded its deadline"):
        subject.detect_track_owned("source.mp4", _LANDSCAPE, 0.01)

    assert process.terminated is True


def test_detect_track_owned_reads_completed_child_result(monkeypatch):
    class FakeProcess:
        exitcode = 0

        def __init__(self, args):
            self.args = args

        def start(self):
            result_path = self.args[0]
            subject.Path(result_path).write_text(
                subject.json.dumps(
                    {
                        "ok": True,
                        "track": [
                            {"t": 0.0, "cx": 320.0, "cy": 180.0, "w": 40.0, "h": 40.0}
                        ],
                        "sample_times": [0.0],
                    }
                ),
                encoding="utf-8",
            )

        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    class FakeContext:
        def Process(self, **kwargs):
            return FakeProcess(kwargs["args"])

    monkeypatch.setattr(subject, "_process_context", lambda: FakeContext())

    track, times = subject.detect_track_owned("source.mp4", _LANDSCAPE, 1.0)

    assert track[0].cx == 320.0
    assert times == [0.0]


def test_build_plan_returns_both_profiles(monkeypatch):
    monkeypatch.setattr(subject, "scene_cuts", lambda *a, **k: [])
    monkeypatch.setattr(subject, "detect_track_owned", lambda *a, **k: ([], []))
    speech, music = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))
    assert speech.profile == "speech"
    assert music.profile == "music"


def test_build_plan_analysis_failed_when_detection_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("detector exploded")

    monkeypatch.setattr(subject, "scene_cuts", lambda *a, **k: [])
    monkeypatch.setattr(subject, "detect_track_owned", boom)
    speech, music = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert speech.decision == "blur_pad"
    assert speech.reason == "analysis_failed"
    assert speech.profile == "speech"
    assert music.decision == "blur_pad"
    assert music.reason == "analysis_failed"
    assert music.profile == "music"


def test_build_plan_analysis_failed_when_scene_cuts_times_out(monkeypatch):
    def timeout(*a, **k):
        raise ProcessTimeoutError(["ffmpeg"], 1.0)

    monkeypatch.setattr(subject, "scene_cuts", timeout)
    speech, music = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert speech.decision == "blur_pad"
    assert speech.reason == "analysis_failed"
    assert music.decision == "blur_pad"
    assert music.reason == "analysis_failed"


def test_build_plan_rejects_scene_result_after_shared_deadline(monkeypatch):
    monkeypatch.setattr(subject, "analysis_timeout", lambda duration: 0.01)

    def late_scene(*args, **kwargs):
        time.sleep(0.02)
        return []

    monkeypatch.setattr(subject, "scene_cuts", late_scene)

    speech, music = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert speech.reason == "analysis_failed"
    assert music.reason == "analysis_failed"


def test_build_plan_analysis_failed_when_model_missing(monkeypatch, tmp_path):
    def missing(*args, **kwargs):
        raise RuntimeError("model missing")

    monkeypatch.setattr(subject, "scene_cuts", lambda *a, **k: [])
    monkeypatch.setattr(subject, "detect_track_owned", missing)
    speech, music = subject.build_plan("source.mp4", MediaInfo(1920, 1080, 10.0, True))

    assert speech.decision == "blur_pad"
    assert speech.reason == "analysis_failed"
    assert music.decision == "blur_pad"
    assert music.reason == "analysis_failed"


def test_analysis_timeout_floor_and_scaling():
    assert subject.analysis_timeout(10.0) == 60.0
    assert subject.analysis_timeout(100.0) == 200.0


def test_yunet_model_runs_with_real_cv2():
    import cv2
    import numpy as np

    detector = cv2.FaceDetectorYN.create(
        str(subject.MODEL_PATH),
        "",
        (subject.DETECT_WIDTH, subject.DETECT_WIDTH),
        subject.SCORE_MIN,
    )
    assert detector is not None
    assert subject.MODEL_PATH.exists()
    detector.setInputSize((640, 360))
    _retval, faces = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
    assert faces is None or faces.shape[1] >= 5


def test_crop_sample_is_even_int_contract():
    # Guards the CropSample x type the plan writes into crop.cmd (geometry F2).
    sample = CropSample(t=0.2, x=100)
    assert sample.x == 100
