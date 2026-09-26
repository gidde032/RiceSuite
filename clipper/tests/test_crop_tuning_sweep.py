"""Tests for the maintainer-only subject-crop tuning sweep."""

import pytest

from app.models import CropPlan, CropSample, TrackSample
from scripts.crop_tuning_sweep import _interpolate_plan, _snap_indices


def _plan() -> CropPlan:
    return CropPlan(
        decision="crop",
        reason="ok",
        face_rate=1.0,
        safe_rate=1.0,
        window_w=608,
        window_h=1080,
        samples=[
            CropSample(t=0.0, x=100),
            CropSample(t=0.2, x=200),
            CropSample(t=0.4, x=500),
        ],
    )


def test_interpolation_densifies_ordinary_move_but_preserves_snap():
    interpolated = _interpolate_plan(_plan(), snap_indices={2}, fps=30)

    before_snap = [sample for sample in interpolated.samples if sample.t < 0.2]
    assert len(before_snap) > 1
    assert [sample.x for sample in before_snap] == sorted(
        sample.x for sample in before_snap
    )
    assert [(sample.t, sample.x) for sample in interpolated.samples[-2:]] == [
        (0.2, 200),
        (0.4, 500),
    ]


def test_interpolation_rejects_nonpositive_fps():
    with pytest.raises(ValueError, match="fps"):
        _interpolate_plan(_plan(), snap_indices=set(), fps=0)


def test_snap_indices_keep_cut_and_face_jump_immediate():
    track = [
        TrackSample(t=0.0, cx=500, cy=540, w=100, h=100),
        TrackSample(t=0.2, cx=520, cy=540, w=100, h=100),
        TrackSample(t=0.4, cx=900, cy=540, w=100, h=100),
        TrackSample(t=0.6, cx=920, cy=540, w=100, h=100),
    ]

    snaps = _snap_indices(
        track,
        [sample.t for sample in track],
        [(0.1, 0.4)],
        1920,
        "speech",
    )

    assert snaps == {1, 2}
