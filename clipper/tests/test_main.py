from __future__ import annotations

import io

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import jobs, main
from app.models import CropPlan, HeaderRequest, RenderRequest, Word
from app.probe import MediaInfo
from render import framing


def _ready_job(root):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"x")
    job.info = MediaInfo(1080, 1920, 2.0, True)
    return job


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


@pytest.mark.smoke
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
    monkeypatch.setattr(
        main,
        "render",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("ffmpeg disappeared")),
    )

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


def test_generate_header_route_returns_header(monkeypatch, isolated_jobs):
    job = _ready_job(isolated_jobs)
    monkeypatch.setattr(main.frame, "grab_frame_b64", lambda *a, **k: "ZmFrZQ==")
    captured: dict = {}

    def fake_gen(transcript, **kwargs):
        captured["transcript"] = transcript
        captured.update(kwargs)
        return "A lovely clip 🎉"

    monkeypatch.setattr(main.header_gen, "generate_header", fake_gen)

    result = main.generate_header(job.id, HeaderRequest(transcript="hello world"))

    assert result == {"header": "A lovely clip 🎉"}
    assert captured["transcript"] == "hello world"
    assert captured["thumbnail_b64"] == "ZmFrZQ=="


def test_generate_header_falls_back_to_transcribed_words(monkeypatch, isolated_jobs):
    job = _ready_job(isolated_jobs)
    job.words = [
        Word(text="hello", start=0.0, end=0.5),
        Word(text="there", start=0.5, end=1.0),
    ]
    monkeypatch.setattr(main.frame, "grab_frame_b64", lambda *a, **k: "")
    captured: dict = {}

    def fake_gen(transcript, **kwargs):
        captured["transcript"] = transcript
        return "H"

    monkeypatch.setattr(main.header_gen, "generate_header", fake_gen)

    main.generate_header(job.id, HeaderRequest())

    assert captured["transcript"] == "hello there"


def test_generate_header_degrades_when_frame_grab_fails(monkeypatch, isolated_jobs):
    job = _ready_job(isolated_jobs)

    def boom(*a, **k):
        raise main.frame.FrameGrabError("no frame")

    monkeypatch.setattr(main.frame, "grab_frame_b64", boom)
    captured: dict = {}

    def fake_gen(transcript, **kwargs):
        captured.update(kwargs)
        return "H"

    monkeypatch.setattr(main.header_gen, "generate_header", fake_gen)

    main.generate_header(job.id, HeaderRequest(transcript="hi"))

    assert captured["thumbnail_b64"] == ""


def test_generate_header_config_error_returns_503(monkeypatch, isolated_jobs):
    job = _ready_job(isolated_jobs)
    monkeypatch.setattr(main.frame, "grab_frame_b64", lambda *a, **k: "")

    def boom(*a, **k):
        raise main.header_gen.HeaderConfigError("no key")

    monkeypatch.setattr(main.header_gen, "generate_header", boom)

    with pytest.raises(HTTPException) as exc_info:
        main.generate_header(job.id, HeaderRequest(transcript="hi"))

    assert exc_info.value.status_code == 503


def test_generate_header_generation_error_returns_502(monkeypatch, isolated_jobs):
    job = _ready_job(isolated_jobs)
    monkeypatch.setattr(main.frame, "grab_frame_b64", lambda *a, **k: "")

    def boom(*a, **k):
        raise main.header_gen.HeaderGenerationError("api down")

    monkeypatch.setattr(main.header_gen, "generate_header", boom)

    with pytest.raises(HTTPException) as exc_info:
        main.generate_header(job.id, HeaderRequest(transcript="hi"))

    assert exc_info.value.status_code == 502


def test_generate_header_missing_job_returns_404(isolated_jobs):
    with pytest.raises(HTTPException) as exc_info:
        main.generate_header("missing", HeaderRequest())
    assert exc_info.value.status_code == 404


def test_generate_header_not_ready_returns_409(isolated_jobs):
    job = jobs.create_job()  # transcribing, no source/info yet
    with pytest.raises(HTTPException) as exc_info:
        main.generate_header(job.id, HeaderRequest())
    assert exc_info.value.status_code == 409


def _landscape_plan() -> CropPlan:
    return CropPlan(
        decision="crop",
        reason="ok",
        face_rate=0.96,
        safe_rate=0.99,
        window_w=608,
        window_h=1080,
    )


def test_transcribe_builds_crop_plan_for_landscape(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1920, 1080, 4.0, True)
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])
    plan = _landscape_plan()
    monkeypatch.setattr(main.subject, "build_plan", lambda *a, **k: plan)

    result = main.transcribe_job(job.id)

    assert result.status == "ready"
    assert result.crop_plan == plan


def test_transcribe_leaves_crop_plan_none_for_vertical(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1080, 1920, 4.0, True)
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])

    def fail(*a, **k):
        raise AssertionError("build_plan must not run for vertical input")

    monkeypatch.setattr(main.subject, "build_plan", fail)

    result = main.transcribe_job(job.id)

    assert result.status == "ready"
    assert result.crop_plan is None


def test_transcribe_ready_even_when_analysis_failed(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1920, 1080, 4.0, True)
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])
    failed = framing.failed_plan("analysis_failed", 1920, 1080)
    monkeypatch.setattr(main.subject, "build_plan", lambda *a, **k: failed)

    result = main.transcribe_job(job.id)

    assert result.status == "ready"
    assert result.crop_plan is not None
    assert result.crop_plan.reason == "analysis_failed"
    assert result.crop_plan.decision == "blur_pad"
