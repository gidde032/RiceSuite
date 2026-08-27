from __future__ import annotations

import os

import pytest

from app import jobs


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
