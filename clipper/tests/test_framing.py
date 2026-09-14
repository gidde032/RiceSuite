"""Tests for the pure framing policy (render.framing, ADR-001).

Each committed track fixture under ``tests/fixtures/tracks/`` drives one
decision assertion. Motion invariants (pan cap, cut snap, loss hold) are
checked directly from the resolved samples. No cv2 or ffmpeg here.
"""

import json
from pathlib import Path

import pytest

from app.models import TrackSample
from render.framing import (
    FACE_RATE_MIN,
    PAN_CAP,
    SAFE_RATE_MIN,
    plan_crop,
    window_size,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tracks"


def _load(name: str):
    """Load a fixture into (track, cuts, source_w, source_h)."""
    data = json.loads((FIXTURES / f"{name}.json").read_text())
    track = [TrackSample(**s) if s is not None else None for s in data["samples"]]
    return track, data["cuts"], data["source_w"], data["source_h"]


def _even_down(value: int) -> int:
    return value if value % 2 == 0 else value - 1


def _expected_x(cx: float, window_w: int, max_x: int) -> int:
    return _even_down(max(0, min(round(cx - window_w / 2), max_x)))


# --- decision per fixture -----------------------------------------------------


@pytest.mark.parametrize(
    ("name", "decision", "reason"),
    [
        ("static", "crop", "ok"),
        ("walking", "crop", "ok"),
        ("two_shot", "crop", "ok"),
        ("cut_heavy", "crop", "ok"),
        ("no_face", "blur_pad", "no_samples"),
        ("lost_then_found", "crop", "ok"),
    ],
)
def test_fixture_decision(name, decision, reason):
    track, cuts, sw, sh = _load(name)
    plan = plan_crop(track, cuts, sw, sh)
    assert plan.decision == decision
    assert plan.reason == reason


def test_no_face_rates_zero():
    track, cuts, sw, sh = _load("no_face")
    plan = plan_crop(track, cuts, sw, sh)
    assert plan.face_rate == 0.0
    assert plan.safe_rate == 0.0


def test_static_holds_x_constant():
    track, cuts, sw, sh = _load("static")
    plan = plan_crop(track, cuts, sw, sh)
    xs = {s.x for s in plan.samples}
    assert len(xs) == 1


def test_walking_is_safe():
    track, cuts, sw, sh = _load("walking")
    plan = plan_crop(track, cuts, sw, sh)
    assert plan.face_rate >= FACE_RATE_MIN
    assert plan.safe_rate >= SAFE_RATE_MIN


def test_two_shot_holds_larger_face():
    # Single-face track: the detector already picked the larger face (cx 500);
    # framing only frames it. The window holds near x 196.
    track, cuts, sw, sh = _load("two_shot")
    plan = plan_crop(track, cuts, sw, sh)
    for s in plan.samples:
        assert abs(s.x - 196) <= 2


# --- motion invariants --------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["static", "walking", "two_shot", "cut_heavy", "lost_then_found"],
)
def test_pan_speed_never_exceeds_cap(name):
    track, cuts, sw, sh = _load(name)
    plan = plan_crop(track, cuts, sw, sh)
    limit = PAN_CAP * sw
    for i in range(1, len(track)):
        # Skip snaps: cut transitions and a face returning after a loss.
        if track[i] is None or track[i - 1] is None:
            continue
        t_prev = plan.samples[i - 1].t
        t_cur = plan.samples[i].t
        if any(t_prev < c <= t_cur for c in cuts):
            continue
        dt = t_cur - t_prev
        speed = abs(plan.samples[i].x - plan.samples[i - 1].x) / dt
        # Tolerance for even-int rounding (up to 2 px per step).
        assert speed <= limit + 2.0 / dt


def test_cut_snaps_to_target():
    track, cuts, sw, sh = _load("cut_heavy")
    plan = plan_crop(track, cuts, sw, sh)
    window_w, _ = window_size(sw, sh)
    max_x = sw - window_w
    snapped = 0
    for i in range(1, len(track)):
        t_prev = plan.samples[i - 1].t
        t_cur = plan.samples[i].t
        if not any(t_prev < c <= t_cur for c in cuts):
            continue
        expected = _expected_x(track[i].cx, window_w, max_x)
        assert plan.samples[i].x == expected
        snapped += 1
    assert snapped == len(cuts)


def test_loss_holds_x():
    track, cuts, sw, sh = _load("lost_then_found")
    plan = plan_crop(track, cuts, sw, sh)
    null_xs = {plan.samples[i].x for i, s in enumerate(track) if s is None}
    assert len(null_xs) == 1


def test_face_returns_after_loss_snaps():
    track, cuts, sw, sh = _load("lost_then_found")
    plan = plan_crop(track, cuts, sw, sh)
    window_w, _ = window_size(sw, sh)
    max_x = sw - window_w
    # First face index after the null run.
    ret = next(
        i for i in range(1, len(track)) if track[i] is not None and track[i - 1] is None
    )
    assert plan.samples[ret].x == _expected_x(track[ret].cx, window_w, max_x)


# --- warning ------------------------------------------------------------------


def test_header_zone_warning():
    track = [TrackSample(t=i * 0.2, cx=960, cy=100, w=140, h=180) for i in range(50)]
    plan = plan_crop(track, [], 1920, 1080)
    assert plan.warning == "header_zone"


# --- window sizing ------------------------------------------------------------


def test_window_size_landscape():
    assert window_size(1920, 1080) == (608, 1080)


def test_non_landscape_source_yields_no_samples():
    # A source that is not wider than tall cannot be cropped to 9:16.
    track = [TrackSample(t=i * 0.2, cx=500, cy=540, w=140, h=180) for i in range(50)]
    plan = plan_crop(track, [], 1000, 1080)
    assert plan.decision == "blur_pad"
    assert plan.reason == "no_samples"
