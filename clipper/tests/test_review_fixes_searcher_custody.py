"""Round 2 regressions for Searcher custody and failed handoff cleanup."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app import handoff, jobs, probe, searcher_pickup
from app.probe import MediaInfo


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch):
    work = tmp_path / "work"
    inbox = tmp_path / "inbox"
    work.mkdir()
    inbox.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", work)
    monkeypatch.setenv("RICECLIPPER_SEARCHER_INBOX", str(inbox))
    monkeypatch.setattr(
        probe,
        "probe",
        lambda _path: MediaInfo(1080, 1920, 10.0, True),
    )
    previous = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield inbox, work
    jobs._JOBS.clear()
    jobs._JOBS.update(previous)


def _write_searcher_batch(inbox: Path) -> dict:
    clip = {
        "file": "clip_1.mp4",
        "position": 1,
        "source_ref": "https://youtu.be/example",
        "source_title": "Episode",
        "source_window": {
            "pad_in": 10,
            "pad_out": 20,
            "target_in": 12,
            "target_out": 18,
        },
        "clip": {"duration": 10, "target_in": 2, "target_out": 8},
        "transcript": "selected words",
        "score": 0.9,
        "rationale": "good moment",
        "rights_risk": "med",
        "beat_profile_version": "v1",
    }
    manifest = {
        "schema_version": 1,
        "batch_id": "batch_restart",
        "created_at": "2026-09-09T00:00:00Z",
        "producer": "ricesearcher",
        "clips": [clip],
    }
    batch = inbox / manifest["batch_id"]
    batch.mkdir()
    (batch / clip["file"]).write_bytes(b"clip")
    (batch / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def test_searcher_job_recovers_after_restart_with_full_metadata(isolated) -> None:
    """Cleanup/custody reviewer (HIGH): consumed jobs survive a restart."""
    inbox, _work = isolated
    manifest = _write_searcher_batch(inbox)
    result = searcher_pickup.pull_next_batch()
    job_id = result["jobs"][0]["id"]

    jobs._JOBS.clear()  # process restart
    recovered = jobs.get_job(job_id)

    assert recovered is not None and recovered.source_path.is_file()
    assert recovered.status == "ready"
    assert recovered.searcher_metadata == manifest["clips"][0]
    assert recovered.searcher_manifest == manifest


def test_consumed_write_retry_reuses_durable_job(isolated, monkeypatch) -> None:
    """Cleanup/custody reviewer (HIGH): receipt failure cannot duplicate jobs."""
    inbox, work = isolated
    _write_searcher_batch(inbox)
    real_save = searcher_pickup._save_consumed
    monkeypatch.setattr(
        searcher_pickup,
        "_save_consumed",
        lambda *_args: (_ for _ in ()).throw(OSError("disk unavailable")),
    )
    with pytest.raises(searcher_pickup.PickupError, match="record consumed"):
        searcher_pickup.pull_next_batch()

    jobs._JOBS.clear()  # fail, then restart before retry
    monkeypatch.setattr(searcher_pickup, "_save_consumed", real_save)
    result = searcher_pickup.pull_next_batch()

    assert result["clip_count"] == 1
    assert len([path for path in work.iterdir() if path.is_dir()]) == 1
    assert not (inbox / "batch_restart").exists()


def test_failed_handoff_removes_only_new_partial_batch(tmp_path: Path) -> None:
    """Cleanup reviewer (MEDIUM): copy failure leaves no invisible orphan."""
    root = tmp_path / "handoff"
    complete = root / "existing"
    complete.mkdir(parents=True)
    (complete / "manifest.json").write_text("{}", encoding="utf-8")
    source = tmp_path / "ready.mp4"
    source.write_bytes(b"rendered")

    with pytest.raises(handoff.HandoffError, match="no rendered output"):
        handoff.write_batch(
            [
                handoff.HandoffEntry(1, source),
                handoff.HandoffEntry(2, tmp_path / "missing.mp4"),
            ],
            root=root,
        )

    assert list(root.iterdir()) == [complete]
