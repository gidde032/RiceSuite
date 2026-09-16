import json
from pathlib import Path

import pytest

from app.models import CropPlan, CropSample, TrackSample
from scripts import crop_check


def test_fixture_roles_require_exactly_one_of_each_role():
    clips = [Path(f"interview-{role}.mp4") for role in crop_check.REQUIRED_ROLES]

    roles = crop_check._fixture_roles(clips)

    assert set(roles.values()) == set(crop_check.REQUIRED_ROLES)


def test_fixture_roles_reject_duplicates_and_incomplete_sets():
    clips = [
        Path("static-a.mp4"),
        Path("static-b.mp4"),
        Path("walking.mp4"),
        Path("two-shot.mp4"),
        Path("cut-heavy.mp4"),
        Path("no-face.mp4"),
    ]

    with pytest.raises(ValueError, match="duplicate fixture role"):
        crop_check._fixture_roles(clips)


def test_governed_pan_ignores_contractual_scene_cut_snap():
    plan = CropPlan(
        decision="crop",
        reason="ok",
        face_rate=1.0,
        safe_rate=1.0,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=0), CropSample(t=0.2, x=1000)],
    )
    track = [
        TrackSample(t=0.0, cx=304, cy=540, w=80, h=80),
        TrackSample(t=0.2, cx=1304, cy=540, w=80, h=80),
    ]

    governed = crop_check._max_governed_pan_px_per_s(
        plan, track, [(0.1, 0.5)], source_w=1920
    )

    assert governed == 0.0


def test_governed_pan_carries_cut_across_missing_sample():
    plan = CropPlan(
        decision="crop",
        reason="ok",
        face_rate=2 / 3,
        safe_rate=1.0,
        window_w=608,
        window_h=1080,
        samples=[
            CropSample(t=0.0, x=0),
            CropSample(t=0.2, x=0),
            CropSample(t=0.4, x=500),
        ],
    )
    track = [
        TrackSample(t=0.0, cx=700, cy=540, w=80, h=80),
        None,
        TrackSample(t=0.4, cx=800, cy=540, w=80, h=80),
    ]

    governed = crop_check._max_governed_pan_px_per_s(plan, track, [(0.1, 0.5)], 1920)

    assert governed == 0.0


def test_fixture_processing_rejects_interlaced_input(tmp_path):
    info = crop_check.MediaInfo(1920, 1080, 2.0, True, field_order="tt")

    with pytest.raises(RuntimeError, match="interlaced input"):
        crop_check._process_one(tmp_path / "static.mp4", info, tmp_path)


def test_fixture_pass_contract_includes_quality_runtime_and_pan_cap():
    row = {
        "decision": "crop",
        "face_rate": 0.8,
        "safe_rate": 0.95,
        "analysis_s": 10.0,
        "pan_cap_ok": True,
    }
    assert crop_check._passes("static", row) is True

    assert crop_check._passes("static", {**row, "analysis_s": 10.01}) is False
    assert crop_check._passes("static", {**row, "pan_cap_ok": False}) is False
    assert crop_check._passes("no_face", {**row, "decision": "blur_pad"}) is True


def test_gate_requires_manual_contact_sheet_attestation(tmp_path, monkeypatch):
    clips = [tmp_path / f"fixture-{role}.mp4" for role in crop_check.REQUIRED_ROLES]
    for clip in clips:
        clip.touch()

    monkeypatch.setattr(
        crop_check,
        "probe",
        lambda _path: crop_check.MediaInfo(1920, 1080, 2.0, True),
    )
    monkeypatch.setattr(
        crop_check,
        "_process_one",
        lambda source, info, out, profile="speech": {
            "name": source.name,
            "width": info.width,
            "height": info.height,
            "duration": info.duration,
            "decision": "blur_pad" if "no_face" in source.name else "crop",
            "reason": "no_samples" if "no_face" in source.name else "ok",
            "face_rate": 0.0 if "no_face" in source.name else 1.0,
            "safe_rate": 0.0 if "no_face" in source.name else 1.0,
            "warning": None,
            "max_pan_px_per_s": 0.0,
            "max_governed_pan_px_per_s": 0.0,
            "pan_cap_ok": True,
            "cuts": 0,
            "hold_spans": 0,
            "analysis_s": 1.0,
        },
    )

    assert crop_check.main([str(tmp_path)]) == 1
    assert crop_check.main([str(tmp_path), "--contact-sheets-approved"]) == 0


def _mock_row(source, info, out, profile="speech"):
    return {
        "name": source.name,
        "width": info.width,
        "height": info.height,
        "duration": info.duration,
        "decision": "crop",
        "reason": "ok",
        "face_rate": 1.0,
        "safe_rate": 1.0,
        "warning": None,
        "max_pan_px_per_s": 0.0,
        "max_governed_pan_px_per_s": 0.0,
        "pan_cap_ok": True,
        "cuts": 0,
        "hold_spans": 0,
        "analysis_s": 1.0,
    }


def test_roles_none_two_clips_writes_two_row_report(tmp_path, monkeypatch):
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for c in clips:
        c.touch()

    monkeypatch.setattr(
        crop_check,
        "probe",
        lambda _p: crop_check.MediaInfo(1920, 1080, 2.0, True),
    )
    monkeypatch.setattr(crop_check, "_process_one", _mock_row)

    out = tmp_path / "out"
    rc = crop_check.main([str(tmp_path), "--roles", "none", "--out", str(out)])

    assert rc == 0
    report = json.loads((out / "report.json").read_text())
    assert len(report) == 2


def test_profile_music_labels_every_row(tmp_path, monkeypatch):
    clips = [tmp_path / "a.mp4", tmp_path / "b.mp4"]
    for c in clips:
        c.touch()

    monkeypatch.setattr(
        crop_check,
        "probe",
        lambda _p: crop_check.MediaInfo(1920, 1080, 2.0, True),
    )
    monkeypatch.setattr(crop_check, "_process_one", _mock_row)

    out = tmp_path / "out"
    crop_check.main(
        [
            str(tmp_path),
            "--roles",
            "none",
            "--profile",
            "music",
            "--out",
            str(out),
        ]
    )

    report = json.loads((out / "report.json").read_text())
    assert all(row["profile"] == "music" for row in report)


def test_hold_spans_counts_long_faceless_runs():
    face = TrackSample(t=0.0, cx=100, cy=100, w=50, h=50)
    track = [
        face,
        None,
        None,
        None,
        None,
        None,
        face,
        None,
        None,
        face,
        None,
        None,
        None,
        None,
        None,
        None,
        face,
    ]
    sample_times = [
        0.0,
        0.2,
        0.4,
        0.6,
        0.8,
        1.5,
        1.7,
        1.9,
        2.1,
        2.3,
        2.5,
        2.7,
        2.9,
        3.1,
        3.3,
        3.8,
        4.0,
    ]

    result = crop_check._hold_spans(track, sample_times)

    assert result == 2
