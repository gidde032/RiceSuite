"""Synthetic observation of actual copy boundaries, never live data."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi.testclient import TestClient
from ricesuite.progress import Progress, ProgressStore

from app import handoff, jobs, main, send_keys
from tests.test_searcher_pickup import _write_batch
from tests.test_searcher_pickup import env as env


@pytest.fixture
def progress_env(env, tmp_path, monkeypatch):
    monkeypatch.setattr(main, "PROGRESS", ProgressStore())
    monkeypatch.setattr(send_keys, "_WRITING", {})
    output = tmp_path / "output"
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(output))
    return env, output


def rendered_payload(count=3):
    clips = []
    for position in range(1, count + 1):
        job = jobs.create_job()
        job.output_path = job.dir / "output.mp4"
        job.output_path.write_bytes(b"synthetic")
        job.status = "done"
        clips.append(
            {"job_id": job.id, "position": position, "header": f"Title {position}"}
        )
    return {"clips": clips, "send_key": "transport-0001"}


@pytest.mark.parametrize("operation", ["pull", "send"])
def test_reads_second_copy_before_post_returns(progress_env, monkeypatch, operation):
    inbox, _output = progress_env
    if operation == "pull":
        _write_batch(
            inbox,
            "batch_0001",
            clips=[
                {"file": f"clip_{i}.mp4", "position": i, "source_title": f"Title {i}"}
                for i in range(1, 4)
            ],
        )
    payload = rendered_payload() if operation == "send" else None
    entered, release = Event(), Event()
    original = handoff.shutil.copy2
    count = 0

    def slow_copy(source, destination):
        nonlocal count
        count += 1
        if count == 2:
            entered.set()
            assert release.wait(5)
        return original(source, destination)

    monkeypatch.setattr(handoff.shutil, "copy2", slow_copy)
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    with ThreadPoolExecutor() as pool:
        if operation == "send":
            future = pool.submit(
                client.post,
                "/api/handoff",
                json={**payload, "observation_id": "attempt-0001"},
            )
        else:
            future = pool.submit(
                client.post,
                "/api/pull-from-searcher?pull_key=transport-0001&observation_id=attempt-0001",
            )
        try:
            assert entered.wait(5)
            progress = client.get(f"/api/progress/{operation}/attempt-0001").json()
            assert not future.done()
            assert progress["status"] == "active"
            assert progress["current"]["title"] == "Title 2"
            assert progress["completed"] == 1 and progress["total"] == 3
            assert not progress["published"]
        finally:
            release.set()
        assert future.result().status_code == 200
    assert (
        client.get(f"/api/progress/{operation}/attempt-0001").json()["status"]
        == "complete"
    )
    other = "send" if operation == "pull" else "pull"
    assert client.get(f"/api/progress/{other}/attempt-0001").status_code == 404


def test_copy_failure_names_item_without_delivery(progress_env, monkeypatch):
    _, output = progress_env
    payload = rendered_payload()
    original = handoff.shutil.copy2
    count = 0

    def fail_copy(source, destination):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic disk error")
        return original(source, destination)

    monkeypatch.setattr(handoff.shutil, "copy2", fail_copy)
    client = TestClient(
        main.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    )
    assert (
        client.post(
            "/api/handoff", json={**payload, "observation_id": "attempt-0001"}
        ).status_code
        == 500
    )
    snapshot = client.get("/api/progress/send/attempt-0001").json()
    assert snapshot["status"] == "failed" and not snapshot["published"]
    assert snapshot["completed"] == 1
    assert [item["state"] for item in snapshot["items"]] == [
        "copied",
        "failed",
        "waiting",
    ]
    assert not list(output.glob("*/manifest.json"))


@pytest.mark.parametrize("failure", ["notify", "finish", "start"])
def test_observer_failure_does_not_fail_work(progress_env, monkeypatch, failure):
    _, output = progress_env
    payload = rendered_payload(1)

    def broken(*args, **kwargs):
        raise RuntimeError("broken observer")

    monkeypatch.setattr(
        ProgressStore if failure == "start" else Progress, failure, broken
    )
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    response = client.post(
        "/api/handoff", json={**payload, "observation_id": "attempt-0001"}
    )
    assert response.status_code == 200
    assert (output / response.json()["batch_id"] / "manifest.json").is_file()


def test_postpublication_ambiguity_and_keyed_replay(progress_env, monkeypatch):
    _, output = progress_env
    payload = rendered_payload(1)
    close = main.searcher_pickup.close_open_batches

    def broken(*args):
        raise RuntimeError("response failed after publication")

    monkeypatch.setattr(main.searcher_pickup, "close_open_batches", broken)
    client = TestClient(
        main.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    )
    assert (
        client.post(
            "/api/handoff", json={**payload, "observation_id": "attempt-0001"}
        ).status_code
        == 500
    )
    first = client.get("/api/progress/send/attempt-0001").json()
    assert first["status"] == "unconfirmed" and first["published"]
    monkeypatch.setattr(main.searcher_pickup, "close_open_batches", close)
    replay = client.post(
        "/api/handoff", json={**payload, "observation_id": "attempt-0002"}
    ).json()
    assert replay["replayed"] and replay["batch_id"] == first["batch_id"]
    assert len(list(output.glob("*/manifest.json"))) == 1
    assert client.get("/api/progress/send/attempt-0002").json()["status"] == "complete"


def test_single_use_attempt_and_unknown(progress_env):
    payload = rendered_payload(1)
    client = TestClient(main.app, base_url="http://127.0.0.1:8000")
    assert (
        client.post(
            "/api/handoff", json={**payload, "observation_id": "attempt-0001"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/handoff", json={**payload, "observation_id": "attempt-0001"}
        ).status_code
        == 409
    )
    assert client.get("/api/progress/send/unknown-attempt").status_code == 404
    assert (
        client.post("/api/pull-from-searcher?observation_id=short").status_code == 422
    )


def test_manifest_publication_failure_preserves_copied_count(progress_env, monkeypatch):
    _, output = progress_env
    payload = rendered_payload()
    original = handoff.os.replace

    def failed_manifest(source, destination):
        if str(destination).endswith("manifest.json"):
            raise OSError("manifest publication failed")
        return original(source, destination)

    monkeypatch.setattr(handoff.os, "replace", failed_manifest)
    client = TestClient(
        main.app, base_url="http://127.0.0.1:8000", raise_server_exceptions=False
    )
    assert (
        client.post(
            "/api/handoff", json={**payload, "observation_id": "attempt-0001"}
        ).status_code
        == 500
    )
    snapshot = client.get("/api/progress/send/attempt-0001").json()
    assert snapshot["status"] == "failed" and not snapshot["published"]
    assert snapshot["completed"] == 3
    assert all(item["state"] == "copied" for item in snapshot["items"])
    assert not list(output.glob("*/manifest.json"))
