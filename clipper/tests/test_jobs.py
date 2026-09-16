from __future__ import annotations

import json
import os

import pytest

from app import jobs
from app.models import CropPlan, CropSample
from app.probe import MediaInfo


def _searcher_job_with_plan(root, crop_plan):
    job = jobs.create_job()
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"video")
    job.info = MediaInfo(
        1024,
        576,
        5.0,
        True,
        coded_width=720,
        coded_height=576,
        rotation=0,
        sample_aspect_ratio=64 / 45,
        field_order="progressive",
    )
    job.searcher_title = "clip"
    job.searcher_metadata = {"id": "c1"}
    job.searcher_manifest = {"batch": "b1"}
    job.crop_plan = crop_plan
    return job


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)

    previous_jobs = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous_jobs)


def test_cache_info_counts_job_files_and_direct_files(isolated_cache):
    root = isolated_cache
    job_dir = root / "job-a"
    job_dir.mkdir()
    (job_dir / "source.mp4").write_bytes(b"1234")
    nested = job_dir / "nested"
    nested.mkdir()
    (nested / "captions.ass").write_bytes(b"56")
    (root / "orphan.bin").write_bytes(b"789")

    assert jobs.cache_info() == {"job_dirs": 1, "files": 3, "total_bytes": 9}


def test_clear_cache_removes_children_preserves_root_and_forgets_jobs(
    isolated_cache, tmp_path
):
    root = isolated_cache
    job = jobs.create_job()
    job.status = "ready"
    (job.dir / "source.mp4").write_bytes(b"123")
    nested = job.dir / "nested"
    nested.mkdir()
    (nested / "captions.ass").write_bytes(b"45")
    (root / "orphan.bin").write_bytes(b"6789")

    outside = tmp_path / "model-cache"
    outside.mkdir()
    (outside / "model.bin").write_bytes(b"model data")

    result = jobs.clear_cache()

    assert result == {
        "job_dirs_removed": 1,
        "files_removed": 3,
        "bytes_removed": 9,
    }
    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert outside.is_dir()
    assert (outside / "model.bin").read_bytes() == b"model data"
    assert jobs.get_job(job.id) is None


def test_clear_cache_unlinks_symlinks_without_following_targets(
    isolated_cache, tmp_path
):
    if not hasattr(os, "symlink"):
        pytest.skip("symlinks are unavailable on this platform")

    root = isolated_cache
    job_dir = root / "job-a"
    job_dir.mkdir()
    outside = tmp_path / "original-browser-file"
    outside.write_bytes(b"keep me")
    (job_dir / "outside-link").symlink_to(outside)

    jobs.clear_cache()

    assert root.is_dir()
    assert list(root.iterdir()) == []
    assert outside.read_bytes() == b"keep me"


def test_clear_cache_refuses_active_jobs(isolated_cache):
    root = isolated_cache
    active = jobs.Job(id="active", dir=root / "active", status="rendering")
    active.dir.mkdir()
    (active.dir / "output.mp4").write_bytes(b"in progress")
    jobs._JOBS[active.id] = active

    assert jobs.has_active_jobs()
    with pytest.raises(jobs.ActiveJobsError):
        jobs.clear_cache()

    assert active.dir.is_dir()
    assert (active.dir / "output.mp4").exists()


def test_persist_recover_round_trips_crop_plan(isolated_cache):
    plan = CropPlan(
        decision="crop",
        reason="ok",
        face_rate=0.9,
        safe_rate=0.99,
        window_w=608,
        window_h=1080,
        samples=[CropSample(t=0.0, x=100), CropSample(t=0.2, x=104)],
        warning="header_zone",
    )
    job = _searcher_job_with_plan(isolated_cache, plan)
    job.words = [jobs.Word(text="hello", start=0.1, end=0.4)]

    jobs.persist_searcher_job(job)
    recovered = jobs._recover_searcher_job(job.dir)

    assert recovered is not None
    assert recovered.crop_plan == plan
    assert recovered.info == job.info
    assert recovered.words == job.words


def test_recover_old_sidecar_without_plan_or_words_yields_defaults(isolated_cache):
    job = _searcher_job_with_plan(isolated_cache, None)

    jobs.persist_searcher_job(job)
    metadata = job.dir / jobs.JOB_METADATA_FILENAME
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    payload.pop("crop_plan")
    payload.pop("words")
    metadata.write_text(json.dumps(payload), encoding="utf-8")
    recovered = jobs._recover_searcher_job(job.dir)

    assert recovered is not None
    assert recovered.crop_plan is None
    assert recovered.words == []
