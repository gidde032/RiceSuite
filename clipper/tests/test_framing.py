"""Tests for the pure framing policy (render.framing, ADR-001).

Each committed track fixture under ``tests/fixtures/tracks/`` drives one
decision assertion. Motion invariants (pan cap, cut snap, loss hold) are
checked directly from the resolved samples. No cv2 or ffmpeg here.
"""

import json
from pathlib import Path

import pytest

from app.models import CropPlan, TrackSample
from render.framing import (
    FACE_RATE_MIN,
    JUMP_CUT,
    PAN_CAP,
    SAFE_RATE_MIN,
    failed_plan,
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
    prev_face_cx: float | None = track[0].cx if track[0] is not None else None
    for i in range(1, len(track)):
        if track[i] is None or track[i - 1] is None:
            if track[i] is not None:
                prev_face_cx = track[i].cx
            continue
        t_prev = plan.samples[i - 1].t
        t_cur = plan.samples[i].t
        if any(t_prev < c <= t_cur for c in cuts):
            prev_face_cx = track[i].cx
            continue
        if prev_face_cx is not None and abs(track[i].cx - prev_face_cx) > JUMP_CUT * sw:
            prev_face_cx = track[i].cx
            continue
        dt = t_cur - t_prev
        speed = abs(plan.samples[i].x - plan.samples[i - 1].x) / dt
        assert speed <= limit + 2.0 / dt
        prev_face_cx = track[i].cx


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


def test_face_return_after_threshold_snaps_without_late_missing_sample():
    sw, sh = 1920, 1080
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=140, h=180),
        None,
        None,
        None,
        None,
        None,
        TrackSample(t=1.2, cx=780, cy=540, w=140, h=180),
    ]

    plan = plan_crop(track, [], sw, sh)
    window_w, _ = window_size(sw, sh)
    assert plan.samples[-1].x == _expected_x(780, window_w, sw - window_w)


def test_scene_cut_during_missing_sample_snaps_next_visible_face():
    sw, sh = 1920, 1080
    face_a = TrackSample(t=0.0, cx=500, cy=540, w=140, h=180)
    face_b = TrackSample(t=0.4, cx=780, cy=540, w=140, h=180)
    track = [face_a, None, face_b] + [
        face_b.model_copy(update={"t": i * 0.2}) for i in range(3, 11)
    ]

    plan = plan_crop(track, [0.1], sw, sh)
    window_w, _ = window_size(sw, sh)
    assert plan.samples[2].x == _expected_x(780, window_w, sw - window_w)


def test_explicit_sample_times_keep_missing_samples_on_capture_timeline():
    fps = 23.976
    sample_times = [i * 5 / fps for i in range(27)]
    track = [TrackSample(t=t, cx=960, cy=540, w=140, h=180) for t in sample_times]
    track[25] = None

    plan = plan_crop(track, [], 1920, 1080, sample_times=sample_times)

    times = [sample.t for sample in plan.samples]
    assert times == sorted(times)
    assert times[25] == pytest.approx(sample_times[25])


def test_cut_snap_clears_lost(self=None):
    """A-1: after a cut snap, the next sample must smooth, not snap again."""
    sw, sh = 1920, 1080
    step = 0.2
    cx_start = 500.0

    # 7 face samples at cx_start, then 8 Nones (1.6 s > LOSS_S), then a
    # face on a cut at cx_start + 200, then face at cx_start + 450
    # (below JUMP_CUT * 1920 = 288 from the return face).
    track: list[TrackSample | None] = []
    for i in range(7):
        track.append(TrackSample(t=i * step, cx=cx_start, cy=540, w=140, h=180))
    null_start = 7 * step
    for _j in range(8):
        track.append(None)
    ret_t = null_start + 8 * step
    track.append(TrackSample(t=ret_t, cx=cx_start + 200, cy=540, w=140, h=180))
    after_t = ret_t + step
    track.append(TrackSample(t=after_t, cx=cx_start + 450, cy=540, w=140, h=180))

    cuts = [ret_t - 0.01]  # cut just before the return sample

    plan = plan_crop(track, cuts, sw, sh)
    ret_idx = 15  # index of the return sample
    after_idx = 16
    dt = step
    cap = PAN_CAP * sw * dt
    move = abs(plan.samples[after_idx].x - plan.samples[ret_idx].x)
    assert move <= cap + 2, f"expected smooth (max {cap + 2}), got snap of {move}"


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


def test_failed_landscape_plan_keeps_center_sample_for_explicit_crop():
    plan = plan_crop([None] * 10, [], 1920, 1080)

    assert plan.decision == "blur_pad"
    assert [(sample.t, sample.x) for sample in plan.samples] == [(0.0, 656)]


# --- face-jump and center-safe (tuning round 2) --------------------------------


def test_face_jump_snaps_without_scene_cut():
    """A face center jump > JUMP_CUT * source_w snaps with no scene cut."""
    sw, sh = 1920, 1080
    step = 0.2
    window_w, _ = window_size(sw, sh)
    max_x = sw - window_w
    cx_a = 500.0
    cx_b = cx_a + 0.20 * sw
    track = [
        TrackSample(t=0.0, cx=cx_a, cy=540, w=140, h=180),
        TrackSample(t=step, cx=cx_b, cy=540, w=140, h=180),
    ]
    plan = plan_crop(track, [], sw, sh)
    assert plan.samples[1].x == _expected_x(cx_b, window_w, max_x)


def test_small_face_move_obeys_cap():
    """A face move below JUMP_CUT does not snap and obeys the pan cap."""
    sw, sh = 1920, 1080
    step = 0.2
    window_w, _ = window_size(sw, sh)
    max_x = sw - window_w
    cx_a = 500.0
    cx_b = 780.0  # 280 px < JUMP_CUT * 1920 = 288
    track = [
        TrackSample(t=0.0, cx=cx_a, cy=540, w=140, h=180),
        TrackSample(t=step, cx=cx_b, cy=540, w=140, h=180),
    ]
    plan = plan_crop(track, [], sw, sh)
    snap_x = _expected_x(cx_b, window_w, max_x)
    assert plan.samples[1].x != snap_x, "should not snap"
    dt = plan.samples[1].t - plan.samples[0].t
    speed = abs(plan.samples[1].x - plan.samples[0].x) / dt
    assert speed <= PAN_CAP * sw + 2.0 / dt


def test_motion_response_eases_ordinary_move_only():
    """The tuning response scales normal motion without weakening cut snaps."""
    sw, sh = 1920, 1080
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=140, h=180),
        TrackSample(t=0.2, cx=700, cy=540, w=140, h=180),
    ]

    current = plan_crop(track, [], sw, sh, dead_zone=0.10, settle_zone=0.0)
    eased = plan_crop(
        track,
        [],
        sw,
        sh,
        motion_response=0.5,
        dead_zone=0.10,
        settle_zone=0.0,
    )

    assert current.samples[1].x == 388
    assert eased.samples[1].x == 296

    snapped = plan_crop(
        track,
        [0.1],
        sw,
        sh,
        motion_response=0.25,
        dead_zone=0.10,
        settle_zone=0.0,
    )
    assert snapped.samples[1].x == 396


def test_production_defaults_use_strong_lock_and_interpolation():
    """Ratified Level 5 is the universal default for new crop plans."""
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=140, h=180),
        TrackSample(t=0.2, cx=700, cy=540, w=140, h=180),
    ]

    plan = plan_crop(track, [], 1920, 1080)

    assert plan.samples[1].x == 334
    assert plan.samples[1].snap is False
    assert plan.interpolation_fps == 30


def test_cut_sample_is_marked_for_immediate_snap():
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=140, h=180),
        TrackSample(t=0.2, cx=700, cy=540, w=140, h=180),
    ]

    plan = plan_crop(track, [0.1], 1920, 1080)

    assert plan.samples[1].snap is True


@pytest.mark.parametrize("response", [0.0, -0.1, 1.01])
def test_motion_response_rejects_out_of_range_values(response):
    with pytest.raises(ValueError, match="motion_response"):
        plan_crop([], [], 1920, 1080, motion_response=response)


def test_lock_hysteresis_settles_inside_inner_zone():
    """An ordinary correction reaches the inner band, not exact center."""
    sw, sh = 1920, 1080
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=140, h=180),
        TrackSample(t=0.2, cx=700, cy=540, w=140, h=180),
    ]

    plan = plan_crop(track, [], sw, sh, dead_zone=0.10, settle_zone=0.05)

    assert plan.samples[1].x == 366


def test_larger_dead_zone_holds_small_subject_shift():
    sw, sh = 1920, 1080
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=140, h=180),
        TrackSample(t=0.2, cx=580, cy=540, w=140, h=180),
    ]

    plan = plan_crop(track, [], sw, sh, dead_zone=0.15, settle_zone=0.075)

    assert plan.samples[1].x == plan.samples[0].x


@pytest.mark.parametrize(
    ("dead_zone", "settle_zone"),
    [(-0.1, 0.0), (0.5, 0.0), (0.1, -0.01), (0.1, 0.11)],
)
def test_lock_hysteresis_rejects_invalid_zones(dead_zone, settle_zone):
    with pytest.raises(ValueError, match="settle_zone"):
        plan_crop(
            [],
            [],
            1920,
            1080,
            dead_zone=dead_zone,
            settle_zone=settle_zone,
        )


def test_wide_face_centered_is_safe():
    """A 400 px face centered in the window is safe under the center rule."""
    sw, sh = 1920, 1080
    cx = float(sw // 2)
    track = [TrackSample(t=i * 0.2, cx=cx, cy=540, w=400, h=180) for i in range(10)]
    plan = plan_crop(track, [], sw, sh)
    assert plan.safe_rate == 1.0


# --- speech backward compatibility -------------------------------------------

_SPEECH_FIXTURES = [
    f.stem for f in sorted(FIXTURES.glob("*.json")) if not f.stem.startswith("music_")
]


@pytest.mark.parametrize("name", _SPEECH_FIXTURES)
def test_speech_profile_unchanged(name):
    """Speech profile output is identical to the pre-change output on every committed track."""
    track, cuts, sw, sh = _load(name)
    old = plan_crop(track, cuts, sw, sh)
    new = plan_crop(track, cuts, sw, sh, profile="speech")
    old_d = old.model_dump()
    new_d = new.model_dump()
    old_d.pop("profile", None)
    new_d.pop("profile", None)
    assert old_d == new_d
    assert new.profile == "speech"


# --- music profile ------------------------------------------------------------


def test_music_faceless_span_holds():
    """Music: a 3 s faceless span mid-track holds x, decision crop/ok."""
    track, cuts, sw, sh = _load("music_faceless_span")
    plan = plan_crop(track, cuts, sw, sh, profile="music")
    assert plan.decision == "crop"
    assert plan.reason == "ok"
    assert plan.profile == "music"
    null_xs = {plan.samples[i].x for i, s in enumerate(track) if s is None}
    assert len(null_xs) == 1


def test_music_no_face_hold_static():
    """Music, no face ever: hold_static, one centered sample, even x."""
    track, cuts, sw, sh = _load("music_no_face")
    plan = plan_crop(track, cuts, sw, sh, profile="music")
    assert plan.decision == "crop"
    assert plan.reason == "hold_static"
    assert plan.face_rate == 0.0
    assert plan.safe_rate == 0.0
    assert plan.profile == "music"
    assert len(plan.samples) == 1
    ww, _ = window_size(sw, sh)
    expected_x = _even_down((sw - ww) // 2)
    assert plan.samples[0].x == expected_x
    assert plan.samples[0].t == 0.0


def test_music_low_face_crops_speech_blurs():
    """Music with face_rate 0.3 and safe_rate 1.0: crop/ok. Speech: blur_pad/low_face_rate."""
    track, cuts, sw, sh = _load("music_low_face")
    music = plan_crop(track, cuts, sw, sh, profile="music")
    assert music.decision == "crop"
    assert music.reason == "ok"
    assert music.profile == "music"

    speech = plan_crop(track, cuts, sw, sh, profile="speech")
    assert speech.decision == "blur_pad"
    assert speech.reason == "low_face_rate"
    assert speech.profile == "speech"


def test_music_loss_return_snaps():
    """Music: face returns after loss and snaps to the target on the first face sample."""
    track, cuts, sw, sh = _load("music_loss_return")
    plan = plan_crop(track, cuts, sw, sh, profile="music")
    ww, _ = window_size(sw, sh)
    max_x = sw - ww
    ret_idx = next(
        i for i in range(1, len(track)) if track[i] is not None and track[i - 1] is None
    )
    expected = _expected_x(track[ret_idx].cx, ww, max_x)
    assert plan.samples[ret_idx].x == expected
    assert plan.decision == "crop"
    assert plan.reason == "ok"


def test_music_cut_at_025_snaps_speech_ignores():
    """A 0.25 cut snaps under music but not speech."""
    sw, sh = 1920, 1080
    ww, _ = window_size(sw, sh)
    max_x = sw - ww
    cx_a, cx_b = 500.0, 750.0
    track = [
        TrackSample(t=0.0, cx=cx_a, cy=540, w=140, h=180),
        TrackSample(t=0.2, cx=cx_b, cy=540, w=140, h=180),
    ] + [
        TrackSample(t=0.2 + (i + 1) * 0.2, cx=cx_b, cy=540, w=140, h=180)
        for i in range(8)
    ]
    cuts = [(0.1, 0.25)]

    music = plan_crop(track, cuts, sw, sh, profile="music")
    assert music.samples[1].x == _expected_x(cx_b, ww, max_x)

    speech = plan_crop(track, cuts, sw, sh, profile="speech")
    assert speech.samples[1].x != _expected_x(cx_b, ww, max_x)


def test_profile_round_trips():
    """profile field round-trips through CropPlan.model_validate."""
    for prof in ("speech", "music"):
        plan = plan_crop(
            [TrackSample(t=0.0, cx=960, cy=540, w=140, h=180)] * 10,
            [],
            1920,
            1080,
            profile=prof,
        )
        restored = CropPlan.model_validate(plan.model_dump())
        assert restored.profile == prof


def test_failed_plan_carries_profile():
    """failed_plan sets profile on the returned plan."""
    for prof in ("speech", "music"):
        plan = failed_plan("analysis_failed", 1920, 1080, profile=prof)
        assert plan.profile == prof


# --- PR-21 triage repairs -----------------------------------------------------


def test_first_face_after_missing_first_sample_snaps():
    # B3: when the first sample misses a face and the subject is off-center, the
    # first acquisition should snap immediately rather than slow-pan from center.
    src_w, src_h = 640, 360
    window_w, _ = window_size(src_w, src_h)
    max_x = src_w - window_w
    track = [None] + [
        TrackSample(t=i * 0.2, cx=600.0, cy=180.0, w=40.0, h=60.0) for i in range(1, 4)
    ]
    plan = plan_crop(track, [], src_w, src_h, profile="speech")
    assert plan.samples[0].snap is False  # no face yet: hold at center
    assert plan.samples[1].snap is True  # first face: snap
    assert plan.samples[1].x == _expected_x(600.0, window_w, max_x)
