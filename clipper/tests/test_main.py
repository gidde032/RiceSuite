from __future__ import annotations

import io

import pytest
from fastapi import HTTPException
from fastapi import UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import jobs
from app import main
from app.models import RenderRequest
from app.probe import MediaInfo


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    previous_jobs = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous_jobs)


def test_media_routes_report_and_clear_server_cache(isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    (job.dir / "source.mp4").write_bytes(b"source")

    assert "/api/media-info" in {route.path for route in main.app.routes}
    assert "/api/media/clear" in {route.path for route in main.app.routes}
    assert main.media_info() == {"job_dirs": 1, "files": 1, "total_bytes": 6}

    result = main.clear_media()

    assert result == {
        "job_dirs_removed": 1,
        "files_removed": 1,
        "bytes_removed": 6,
    }
    assert isolated_jobs.is_dir()
    assert list(isolated_jobs.iterdir()) == []


def test_media_routes_work_over_http(isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    (job.dir / "source.mp4").write_bytes(b"source")

    with TestClient(main.app) as client:
        info = client.get("/api/media-info")
        cleared = client.post("/api/media/clear")

    assert info.status_code == 200
    assert info.json() == {"job_dirs": 1, "files": 1, "total_bytes": 6}
    assert cleared.status_code == 200
    assert cleared.json()["job_dirs_removed"] == 1


def test_media_clear_route_returns_conflict_while_job_is_active(isolated_jobs):
    job = jobs.Job(id="active", dir=isolated_jobs / "active", status="rendering")
    job.dir.mkdir()
    jobs._JOBS[job.id] = job

    with pytest.raises(HTTPException) as exc_info:
        main.clear_media()

    assert exc_info.value.status_code == 409


def test_render_failure_does_not_leave_job_active(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1080, 1920, 1.0, False)
    monkeypatch.setattr(main, "render", lambda *args, **kwargs: (_ for _ in ()).throw(
        OSError("ffmpeg disappeared")
    ))

    with pytest.raises(HTTPException) as exc_info:
        main.render_job(job.id, RenderRequest())

    assert exc_info.value.status_code == 500
    assert job.status == "error"
    assert job.error == "render failed"
    assert not jobs.has_active_jobs()


def test_duplicate_active_work_is_rejected(isolated_jobs):
    job = jobs.Job(id="active", dir=isolated_jobs / "active", status="rendering")
    job.dir.mkdir()
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1080, 1920, 1.0, False)
    jobs._JOBS[job.id] = job

    with pytest.raises(HTTPException) as exc_info:
        main.render_job(job.id, RenderRequest())

    assert exc_info.value.status_code == 409


def test_upload_probe_failure_returns_client_error_without_local_path(
    monkeypatch, isolated_jobs
):
    def fail_probe(path):
        raise main.probe.ProbeError(f"bad media at {path}")

    monkeypatch.setattr(main.probe, "probe", fail_probe)
    file = UploadFile(filename="clip.mp4", file=io.BytesIO(b"not video"))

    with pytest.raises(HTTPException) as exc_info:
        main.upload(file)

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "could not read video"
    assert str(isolated_jobs) not in str(exc_info.value.detail)


def test_successful_transcription_clears_previous_error(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "error"
    job.error = "previous transcription failed"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])

    result = main.transcribe_job(job.id)

    assert result.status == "ready"
    assert result.error is None


def test_failed_render_can_be_retried(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "error"
    job.error = "previous render failed"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1080, 1920, 1.0, False)
    monkeypatch.setattr(main, "render", lambda *args, **kwargs: job.dir / "output.mp4")

    result = main.render_job(job.id, RenderRequest())

    assert result.status == "done"
    assert result.error is None


def test_render_request_rejects_unknown_visual_presets():
    with pytest.raises(ValidationError):
        RenderRequest(caption_style="not-a-style")
    with pytest.raises(ValidationError):
        RenderRequest(header_style="not-a-header")
