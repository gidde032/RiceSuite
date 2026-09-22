from __future__ import annotations

import io
import threading

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import jobs, main
from app.models import (
    CropPlan,
    CropSample,
    HeaderRequest,
    LyricsRequest,
    LyricsResult,
    RenderRequest,
    Word,
)
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


def _ready_render_job() -> jobs.Job:
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1080, 1920, 1.0, False)
    return job


def test_render_does_not_hold_global_lock(monkeypatch, isolated_jobs):
    # Issue #30: render() must run with the global lock free so batch renders do
    # not serialize behind one another. Probe the lock from a separate thread —
    # the request thread holds an RLock reentrantly, so a same-thread probe would
    # falsely pass.
    job = _ready_render_job()
    observed = {}

    def probing_render(work_dir, *args, **kwargs):
        holder = {}

        def probe():
            got = jobs._JOBS_LOCK.acquire(blocking=False)
            holder["free"] = got
            if got:
                jobs._JOBS_LOCK.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        observed["free"] = holder["free"]
        out = work_dir / "output.mp4"
        out.write_bytes(b"out")
        return out

    monkeypatch.setattr(main, "render", probing_render)

    result = main.render_job(job.id, RenderRequest())

    assert result.status == "done"
    assert observed["free"] is True


def test_two_jobs_render_concurrently(monkeypatch, isolated_jobs):
    # Two different jobs must render at the same time. A barrier forces both
    # mocks to be inside render() together; if the global lock still serialized
    # them, the second never arrives and the barrier times out.
    job_a = _ready_render_job()
    job_b = _ready_render_job()
    both_inside = threading.Barrier(2, timeout=5)

    def concurrent_render(work_dir, *args, **kwargs):
        both_inside.wait()
        out = work_dir / "output.mp4"
        out.write_bytes(b"out")
        return out

    monkeypatch.setattr(main, "render", concurrent_render)
    results: dict[str, object] = {}

    def run(job):
        results[job.id] = main.render_job(job.id, RenderRequest())

    threads = [
        threading.Thread(target=run, args=(job_a,)),
        threading.Thread(target=run, args=(job_b,)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results[job_a.id].status == "done"
    assert results[job_b.id].status == "done"


def test_second_render_of_same_job_returns_409(monkeypatch, isolated_jobs):
    # A render already in flight for a job must reject a second request with 409.
    job = _ready_render_job()
    in_render = threading.Event()
    release = threading.Event()

    def blocking_render(work_dir, *args, **kwargs):
        in_render.set()
        release.wait(timeout=5)
        out = work_dir / "output.mp4"
        out.write_bytes(b"out")
        return out

    monkeypatch.setattr(main, "render", blocking_render)
    holder: dict[str, object] = {}

    def run():
        holder["result"] = main.render_job(job.id, RenderRequest())

    worker = threading.Thread(target=run)
    worker.start()
    assert in_render.wait(timeout=5)

    with pytest.raises(HTTPException) as exc_info:
        main.render_job(job.id, RenderRequest())
    assert exc_info.value.status_code == 409

    release.set()
    worker.join()
    assert holder["result"].status == "done"


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
        samples=[CropSample(t=0.0, x=0)],
        profile="speech",
    )


def _music_plan() -> CropPlan:
    return CropPlan(
        decision="crop",
        reason="ok",
        face_rate=0.96,
        safe_rate=0.99,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=0)],
        profile="music",
    )


def test_transcribe_builds_both_plans_for_landscape(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1920, 1080, 4.0, True)
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])
    speech = _landscape_plan()
    music = _music_plan()
    monkeypatch.setattr(main.subject, "build_plan", lambda *a, **k: (speech, music))

    result = main.transcribe_job(job.id)

    assert result.status == "ready"
    assert result.crop_plan == speech
    assert result.music_plan == music


def test_transcribe_persists_searcher_both_plans(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1920, 1080, 4.0, True)
    job.searcher_title = "clip"
    job.searcher_metadata = {"id": "c1"}
    job.searcher_manifest = {"batch": "b1"}
    jobs.persist_searcher_job(job)
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])
    speech = _landscape_plan()
    music = _music_plan()
    monkeypatch.setattr(main.subject, "build_plan", lambda *a, **k: (speech, music))

    main.transcribe_job(job.id)
    jobs._JOBS.pop(job.id)
    recovered = jobs.get_job(job.id)

    assert recovered is not None
    assert recovered.crop_plan == speech
    assert recovered.music_plan == music


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
    assert result.music_plan is None


def test_transcribe_ready_even_when_analysis_failed(monkeypatch, isolated_jobs):
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1920, 1080, 4.0, True)
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: [])
    failed_speech = framing.failed_plan("analysis_failed", 1920, 1080, profile="speech")
    failed_music = framing.failed_plan("analysis_failed", 1920, 1080, profile="music")
    monkeypatch.setattr(
        main.subject, "build_plan", lambda *a, **k: (failed_speech, failed_music)
    )

    result = main.transcribe_job(job.id)

    assert result.status == "ready"
    assert result.crop_plan is not None
    assert result.crop_plan.reason == "analysis_failed"
    assert result.crop_plan.decision == "blur_pad"
    assert result.music_plan is not None
    assert result.music_plan.reason == "analysis_failed"
    assert result.music_plan.profile == "music"


def _landscape_ready_job():
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1920, 1080, 4.0, True)
    return job


def test_render_crop_on_vertical_job_returns_400(isolated_jobs):
    job = _ready_job(isolated_jobs)  # 1080x1920, no crop plan
    with pytest.raises(HTTPException) as exc:
        main.render_job(job.id, RenderRequest(geometry="crop"))
    assert exc.value.status_code == 400
    assert job.status == "ready"


def test_render_crop_on_analysis_failed_plan_uses_centered_override(
    monkeypatch, isolated_jobs
):
    job = _landscape_ready_job()
    job.crop_plan = framing.failed_plan("analysis_failed", 1920, 1080)
    captured: dict = {}

    def fake_render(*args, **kwargs):
        captured["plan"] = kwargs.get("plan")
        return job.dir / "output.mp4"

    monkeypatch.setattr(main, "render", fake_render)

    main.render_job(job.id, RenderRequest(geometry="crop"))

    assert captured["plan"].decision == "crop"
    assert [(sample.t, sample.x) for sample in captured["plan"].samples] == [(0.0, 656)]


def test_render_crop_on_landscape_passes_crop_plan_to_render(
    monkeypatch, isolated_jobs
):
    job = _landscape_ready_job()
    job.crop_plan = _landscape_plan()
    captured: dict = {}

    def fake_render(*args, **kwargs):
        captured["plan"] = kwargs.get("plan")
        return job.dir / "output.mp4"

    monkeypatch.setattr(main, "render", fake_render)

    main.render_job(job.id, RenderRequest(geometry="crop"))

    assert captured["plan"] is not None
    assert captured["plan"].decision == "crop"


def test_render_default_payload_geometry_is_auto():
    assert RenderRequest().geometry == "auto"


def test_render_request_default_content_is_speech():
    assert RenderRequest().content == "speech"


def test_render_request_accepts_music_content():
    req = RenderRequest(content="music")
    assert req.content == "music"


def test_render_content_music_uses_music_plan(monkeypatch, isolated_jobs):
    job = _landscape_ready_job()
    speech = _landscape_plan()
    music = _music_plan()
    job.crop_plan = speech
    job.music_plan = music
    captured: dict = {}

    def fake_render(*args, **kwargs):
        captured["plan"] = kwargs.get("plan")
        return job.dir / "output.mp4"

    monkeypatch.setattr(main, "render", fake_render)

    main.render_job(job.id, RenderRequest(content="music", geometry="auto"))

    assert captured["plan"] is not None
    assert captured["plan"].profile == "music"


def test_render_content_speech_uses_speech_plan(monkeypatch, isolated_jobs):
    job = _landscape_ready_job()
    speech = CropPlan(
        decision="blur_pad",
        reason="low_face_rate",
        face_rate=0.30,
        safe_rate=0.50,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=0)],
        profile="speech",
    )
    music = _music_plan()
    job.crop_plan = speech
    job.music_plan = music
    captured: dict = {}

    def fake_render(*args, **kwargs):
        captured["plan"] = kwargs.get("plan")
        return job.dir / "output.mp4"

    monkeypatch.setattr(main, "render", fake_render)

    main.render_job(job.id, RenderRequest(content="speech", geometry="auto"))

    assert captured["plan"] is None


def test_render_content_music_no_music_plan_returns_400(isolated_jobs):
    job = _landscape_ready_job()
    job.crop_plan = _landscape_plan()
    job.music_plan = None

    with pytest.raises(HTTPException) as exc:
        main.render_job(job.id, RenderRequest(content="music", geometry="crop"))
    assert exc.value.status_code == 400


# --- lyrics alignment (reference_words) --------------------------------------


def _ready_job_with_words(root):
    job = _ready_job(root)
    whisper_words = [
        Word(text="hello", start=0.1, end=0.4),
        Word(text="world", start=0.5, end=0.9),
    ]
    job.words = list(whisper_words)
    job.reference_words = list(whisper_words)
    return job


def test_repeated_align_uses_reference_words(monkeypatch, isolated_jobs):
    job = _ready_job_with_words(isolated_jobs)
    original_ref = list(job.reference_words)
    lyric_words_1 = [Word(text="hey", start=0.1, end=0.5)]
    lyric_words_2 = [Word(text="yo", start=0.2, end=0.6)]
    call_count = [0]
    captured_refs = []

    def fake_align(text, reference, duration):
        captured_refs.append(list(reference))
        result_words = lyric_words_1 if call_count[0] == 0 else lyric_words_2
        call_count[0] += 1
        return LyricsResult(words=result_words, anchor_rate=0.5, method="anchors")

    monkeypatch.setattr("app.main.lyrics.align", fake_align)

    main.lyrics_job(job.id, LyricsRequest(lyrics="hey"))
    main.lyrics_job(job.id, LyricsRequest(lyrics="yo"))

    assert captured_refs[0] == original_ref
    assert captured_refs[1] == original_ref
    assert job.words == lyric_words_2


def test_align_success_sets_ready_and_invalidates_output(monkeypatch, isolated_jobs):
    job = _ready_job_with_words(isolated_jobs)
    job.status = "done"
    output = job.dir / "output.mp4"
    output.write_bytes(b"rendered")
    job.output_path = output

    def fake_align(text, reference, duration):
        return LyricsResult(
            words=[Word(text="a", start=0.0, end=0.5)],
            anchor_rate=1.0,
            method="anchors",
        )

    monkeypatch.setattr("app.main.lyrics.align", fake_align)

    result = main.lyrics_job(job.id, LyricsRequest(lyrics="a"))

    assert job.status == "ready"
    assert not job.state().has_output
    assert not output.exists()
    assert result.words == [Word(text="a", start=0.0, end=0.5)]


def test_align_failure_leaves_words_and_output_unchanged(monkeypatch, isolated_jobs):
    job = _ready_job_with_words(isolated_jobs)
    job.status = "done"
    output = job.dir / "output.mp4"
    output.write_bytes(b"rendered")
    job.output_path = output
    original_words = list(job.words)

    def bad_align(text, reference, duration):
        raise ValueError("bad lyrics")

    monkeypatch.setattr("app.main.lyrics.align", bad_align)

    with pytest.raises(HTTPException) as exc:
        main.lyrics_job(job.id, LyricsRequest(lyrics="bad"))
    assert exc.value.status_code == 422
    assert job.words == original_words
    assert output.exists()


# --- restore-transcript -------------------------------------------------------


def test_restore_transcript_404_unknown_job(isolated_jobs):
    with pytest.raises(HTTPException) as exc:
        main.restore_transcript("no-such-id")
    assert exc.value.status_code == 404


def test_restore_transcript_409_while_active(isolated_jobs):
    job = _ready_job_with_words(isolated_jobs)
    job.status = "transcribing"
    with pytest.raises(HTTPException) as exc:
        main.restore_transcript(job.id)
    assert exc.value.status_code == 409


def test_restore_transcript_409_empty_reference(isolated_jobs):
    job = _ready_job(isolated_jobs)
    job.reference_words = []
    with pytest.raises(HTTPException) as exc:
        main.restore_transcript(job.id)
    assert exc.value.status_code == 409


def test_restore_transcript_success(isolated_jobs):
    job = _ready_job_with_words(isolated_jobs)
    job.words = [Word(text="lyric", start=0.0, end=1.0)]
    job.status = "done"
    output = job.dir / "output.mp4"
    output.write_bytes(b"rendered")
    job.output_path = output

    state = main.restore_transcript(job.id)

    assert job.words == job.reference_words
    assert state.status == "ready"
    assert not state.has_output
    assert not output.exists()


def test_render_reports_missing_emoji_header_font(monkeypatch, isolated_jobs):
    """Issue #3: the missing-font reason reaches the UI, not just the log."""
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    job.info = MediaInfo(1080, 1920, 1.0, False)
    reason = "missing a renderable color-emoji font for the emoji header"

    def fail(*_args, **_kwargs):
        raise main.HeaderFontError(reason)

    monkeypatch.setattr(main, "render", fail)

    with pytest.raises(HTTPException) as exc_info:
        main.render_job(job.id, RenderRequest(header="hello \U0001f525"))

    assert exc_info.value.status_code == 500
    assert exc_info.value.detail == reason
    assert job.status == "error"
    assert job.error == reason
    assert not jobs.has_active_jobs()
