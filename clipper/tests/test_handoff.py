from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import handoff, jobs, main


def _entry(pos: int, src) -> handoff.HandoffEntry:
    return handoff.HandoffEntry(position=pos, source=src, transcript=f"t{pos}", header=f"h{pos}")


# --- writer -----------------------------------------------------------------


def test_write_batch_lays_out_clips_and_manifest(tmp_path):
    src1 = tmp_path / "a.mp4"
    src1.write_bytes(b"one")
    src2 = tmp_path / "b.mp4"
    src2.write_bytes(b"two")
    root = tmp_path / "handoff"

    # Pass out of order to prove positions, not argument order, drive layout.
    result = handoff.write_batch([_entry(2, src2), _entry(1, src1)], root=root)

    assert result["clip_count"] == 2
    batch_dir = root / result["batch_id"]
    assert (batch_dir / "clip_1.mp4").read_bytes() == b"one"
    assert (batch_dir / "clip_2.mp4").read_bytes() == b"two"

    manifest = json.loads((batch_dir / "manifest.json").read_text())
    assert manifest["schema_version"] == handoff.SCHEMA_VERSION
    assert manifest["producer"] == "riceclipper"
    assert manifest["batch_id"] == result["batch_id"]
    assert [c["position"] for c in manifest["clips"]] == [1, 2]
    assert manifest["clips"][0]["file"] == "clip_1.mp4"
    assert manifest["clips"][0]["transcript"] == "t1"
    assert manifest["clips"][0]["presets"] == {
        "caption_style": "classic",
        "header_style": "plain",
    }


def test_manifest_is_written_last_with_no_temp_left(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    result = handoff.write_batch([_entry(1, src)], root=tmp_path / "handoff")
    batch_dir = tmp_path / "handoff" / result["batch_id"]
    names = {p.name for p in batch_dir.iterdir()}
    assert "manifest.json" in names
    # Atomic rename leaves no partial manifest behind.
    assert "manifest.json.tmp" not in names


def test_write_batch_rejects_empty(tmp_path):
    with pytest.raises(handoff.HandoffError):
        handoff.write_batch([], root=tmp_path / "handoff")


def test_write_batch_rejects_duplicate_positions(tmp_path):
    src = tmp_path / "a.mp4"
    src.write_bytes(b"x")
    with pytest.raises(handoff.HandoffError):
        handoff.write_batch([_entry(1, src), _entry(1, src)], root=tmp_path / "handoff")


def test_write_batch_rejects_missing_source(tmp_path):
    with pytest.raises(handoff.HandoffError):
        handoff.write_batch([_entry(1, tmp_path / "missing.mp4")], root=tmp_path / "handoff")


def test_handoff_root_honours_env(monkeypatch, tmp_path):
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(tmp_path / "custom"))
    assert handoff.handoff_root() == tmp_path / "custom"


# --- endpoint ---------------------------------------------------------------


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    previous = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous)


def _rendered_job() -> jobs.Job:
    job = jobs.create_job()
    job.status = "done"
    out = job.dir / "output.mp4"
    out.write_bytes(b"rendered")
    job.output_path = out
    return job


def test_handoff_endpoint_writes_batch(isolated_jobs, tmp_path, monkeypatch):
    handoff_root = tmp_path / "handoff"
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(handoff_root))
    job = _rendered_job()

    with TestClient(main.app) as client:
        res = client.post(
            "/api/handoff",
            json={
                "clips": [
                    {
                        "job_id": job.id,
                        "position": 1,
                        "transcript": "hello world",
                        "header": "Hook",
                        "caption_style": "punch",
                        "header_style": "black_plate",
                    }
                ]
            },
        )

    assert res.status_code == 200
    body = res.json()
    batch_dir = handoff_root / body["batch_id"]
    assert (batch_dir / "clip_1.mp4").read_bytes() == b"rendered"
    manifest = json.loads((batch_dir / "manifest.json").read_text())
    assert manifest["clips"][0]["transcript"] == "hello world"
    assert manifest["clips"][0]["presets"]["caption_style"] == "punch"


def test_handoff_endpoint_rejects_job_without_output(isolated_jobs, tmp_path, monkeypatch):
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(tmp_path / "handoff"))
    job = jobs.create_job()
    job.status = "ready"  # no rendered output

    with TestClient(main.app) as client:
        res = client.post("/api/handoff", json={"clips": [{"job_id": job.id, "position": 1}]})

    assert res.status_code == 409


def test_handoff_endpoint_rejects_empty_batch(isolated_jobs):
    with TestClient(main.app) as client:
        res = client.post("/api/handoff", json={"clips": []})
    assert res.status_code == 400


def test_handoff_endpoint_unknown_job(isolated_jobs, tmp_path, monkeypatch):
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(tmp_path / "handoff"))
    with TestClient(main.app) as client:
        res = client.post("/api/handoff", json={"clips": [{"job_id": "nope", "position": 1}]})
    assert res.status_code == 404
