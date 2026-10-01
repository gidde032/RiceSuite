"""Temporary, real request execution proves observation never controls work."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from ricesuite.progress import Progress

from ricesearcher.handoff import writer
from ricesearcher.library.store import LEGACY_PROFILE_ID, Library
from ricesearcher.models import CandidateSlice, SliceStatus
from ricesearcher.web.app import create_app
from tests.test_handoff import FakeExtractor, _lib_with_selected


def setup_client(tmp_path, monkeypatch, extractor=FakeExtractor):
    cfg, lib = _lib_with_selected(tmp_path)
    for item_id, score in [("second", 0.8), ("third", 0.7)]:
        lib.upsert_slices(
            [
                CandidateSlice(
                    id=item_id,
                    source_id="s1",
                    target_in=5,
                    target_out=20,
                    pad_in=0,
                    pad_out=30,
                    transcript_span="moment",
                    score=score,
                    status=SliceStatus.SELECTED,
                    profile_id=LEGACY_PROFILE_ID,
                )
            ]
        )
    lib.close()
    monkeypatch.setattr(writer, "FfmpegClipExtractor", extractor)
    monkeypatch.setattr(writer, "ffprobe_duration", lambda _path: 30)
    return cfg, TestClient(
        create_app(cfg), base_url="http://127.0.0.1:8765", raise_server_exceptions=False
    )


def progress(client, attempt="attempt-1", profile=LEGACY_PROFILE_ID):
    return client.get(f"/api/handoff/progress/{attempt}", params={"profile": profile})


def send(client, attempt="attempt-1"):
    return client.post(
        "/api/handoff", json={"profile": LEGACY_PROFILE_ID, "observation_id": attempt}
    )


def test_progress_read_remains_responsive_during_second_clip_and_commit(
    tmp_path, monkeypatch
):
    extracting, committing, release_extract, release_commit = (
        Event() for _ in range(4)
    )

    class BlockedExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            if len(self.calls) == 1:
                extracting.set()
                assert release_extract.wait(10)
            return super().extract(source, start, end, dest)

    publish = writer._publish_batch

    def blocked_publish(batch):
        committing.set()
        assert release_commit.wait(10)
        publish(batch)

    cfg, client = setup_client(tmp_path, monkeypatch, BlockedExtractor)
    monkeypatch.setattr(writer, "_publish_batch", blocked_publish)
    with ThreadPoolExecutor(max_workers=2) as pool:
        request = pool.submit(send, client)
        try:
            assert extracting.wait(5)
            snapshot = pool.submit(progress, client).result(timeout=2).json()
            assert not request.done()
            assert snapshot["status"] == "active"
            assert snapshot["current"]["id"] == "second"
            assert snapshot["completed"] == 1 and snapshot["total"] == 3
            assert not snapshot["published"]
            assert list(cfg.handoff_dir.glob("*/manifest.json")) == []
            release_extract.set()
            assert committing.wait(5)
            snapshot = pool.submit(progress, client).result(timeout=2).json()
            assert snapshot["stage"] == "committing" and snapshot["completed"] == 3
            assert not snapshot["published"]
            assert list(cfg.handoff_dir.glob("*/manifest.json")) == []
        finally:
            release_extract.set()
            release_commit.set()
        assert request.result(timeout=5).status_code == 200
    result = progress(client).json()
    assert result["status"] == "complete" and result["published"]
    assert {it["state"] for it in result["items"]} == {"complete"}


@pytest.mark.parametrize("failure", ["prepare", "publish", "mark"])
def test_failure_boundary_distinguishes_publication(tmp_path, monkeypatch, failure):
    class BrokenExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            if len(self.calls) == 1:
                raise OSError("clip preparation unavailable")
            return super().extract(source, start, end, dest)

    cfg, client = setup_client(
        tmp_path,
        monkeypatch,
        BrokenExtractor if failure == "prepare" else FakeExtractor,
    )

    def fail(*args, **kwargs):
        raise OSError("boundary failed")

    if failure == "publish":
        monkeypatch.setattr(writer, "_publish_batch", fail)
    if failure == "mark":
        monkeypatch.setattr(Library, "bulk_update_status", fail)
    result = send(client)
    assert result.status_code == 503
    snapshot = progress(client).json()
    assert snapshot["status"] == ("unconfirmed" if failure == "mark" else "failed")
    assert snapshot["published"] is (failure == "mark")
    assert snapshot["completed"] == (1 if failure == "prepare" else 3)
    assert not list(
        cfg.handoff_dir.glob("*/manifest.json")
    )  # original cleanup contract
    with Library(cfg.db_path) as lib:
        assert lib.get_slice("a").status is SliceStatus.SELECTED
    if failure == "mark":
        assert "publication occurred" in result.json()["detail"]
        assert snapshot["batch_id"]


def test_attempt_validation_scope_and_single_use(tmp_path, monkeypatch):
    _, client = setup_client(tmp_path, monkeypatch)
    for invalid in ["../bad", "", "a" * 65, "spaces are invalid"]:
        assert (
            client.post(
                "/api/handoff",
                json={"profile": LEGACY_PROFILE_ID, "observation_id": invalid},
            ).status_code
            == 422
        )
    assert send(client).status_code == 200
    assert progress(client, profile="other").status_code == 404
    assert progress(client, "missing").status_code == 404
    assert send(client).status_code == 409
    assert progress(client).json()["total"] == 3
    assert send(client, "attempt-2").json()["clip_count"] == 0
    assert progress(client, "attempt-2").json()["total"] == 0
    assert progress(client).json()["total"] == 3


def test_broken_observer_does_not_fail_real_work(tmp_path, monkeypatch):
    cfg, client = setup_client(tmp_path, monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("observer unavailable")

    monkeypatch.setattr(Progress, "notify", broken)
    monkeypatch.setattr(Progress, "finish", broken)
    assert send(client).status_code == 200
    assert len(list(cfg.handoff_dir.glob("*/manifest.json"))) == 1
    with Library(cfg.db_path) as lib:
        assert lib.get_slice("a").status is SliceStatus.HANDED_OFF


def test_selection_change_clears_unpublished_batch_identity(tmp_path, monkeypatch):
    extracting, release = Event(), Event()

    class BlockedExtractor(FakeExtractor):
        def extract(self, source, start, end, dest):
            extracting.set()
            assert release.wait(10)
            return super().extract(source, start, end, dest)

    cfg, client = setup_client(tmp_path, monkeypatch, BlockedExtractor)
    with ThreadPoolExecutor(max_workers=1) as pool:
        request = pool.submit(send, client)
        try:
            assert extracting.wait(5)
            with Library(cfg.db_path) as lib:
                lib.update_slice_window("a", 6, 24)
        finally:
            release.set()
        assert request.result(timeout=5).json() == {"batch_id": None, "clip_count": 0}
    snapshot = progress(client).json()
    assert snapshot["status"] == "complete"
    assert not snapshot["published"]
    assert snapshot["batch_id"] == ""
    assert "no batch handed off" in snapshot["detail"]
    assert not list(cfg.handoff_dir.glob("*/manifest.json"))
