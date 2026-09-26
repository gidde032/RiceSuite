"""RiceSearcher → RiceClipper pickup (consumer side)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import jobs, main, probe, searcher_pickup
from app.probe import MediaInfo


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated work root, job store, searcher inbox, and a stub probe."""
    work = tmp_path / ".riceclipper_work"
    work.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", work)
    inbox = tmp_path / "ricesearcher-handoff"
    inbox.mkdir()
    monkeypatch.setenv("RICECLIPPER_SEARCHER_INBOX", str(inbox))
    monkeypatch.setattr(
        probe,
        "probe",
        lambda _p: MediaInfo(width=1080, height=1920, duration=10.0, has_audio=True),
    )
    saved = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield inbox
    jobs._JOBS.clear()
    jobs._JOBS.update(saved)


def _write_batch(
    inbox: Path,
    batch_id: str,
    *,
    created_at="2026-09-04T00:00:00Z",
    clips=None,
    producer="ricesearcher",
    schema_version=1,
) -> Path:
    bd = inbox / batch_id
    bd.mkdir()
    if clips is None:
        clips = [
            {
                "file": "clip_1.mp4",
                "position": 1,
                "source_title": "Ep One",
                "transcript": "hi",
                "clip": {"duration": 10, "target_in": 2, "target_out": 8},
            }
        ]
    for c in clips:
        (bd / c["file"]).write_bytes(b"clipbytes")
    manifest = {
        "schema_version": schema_version,
        "batch_id": batch_id,
        "created_at": created_at,
        "producer": producer,
        "clips": clips,
    }
    (bd / "manifest.json").write_text(json.dumps(manifest))
    return bd


def test_pull_ingests_batch_into_jobs(env: Path) -> None:
    bd = _write_batch(env, "batch_a")
    res = searcher_pickup.pull_next_batch()
    assert res["batch_id"] == "batch_a" and res["clip_count"] == 1
    job = res["jobs"][0]
    assert job["status"] == "ready" and job["title"] == "Ep One"
    # A real job exists with the copied clip as its source.
    j = jobs.get_job(job["id"])
    assert j is not None and j.source_path.is_file()
    # Custody transferred: the source batch is removed and recorded consumed.
    assert not bd.exists()
    assert "batch_a" in searcher_pickup._load_consumed(env)


def test_pull_is_fifo_and_dedupes(env: Path) -> None:
    _write_batch(env, "batch_new", created_at="2026-09-04T02:00:00Z")
    _write_batch(env, "batch_old", created_at="2026-09-04T01:00:00Z")
    first = searcher_pickup.pull_next_batch()
    assert first["batch_id"] == "batch_old"  # oldest first
    second = searcher_pickup.pull_next_batch()
    assert second["batch_id"] == "batch_new"
    third = searcher_pickup.pull_next_batch()
    assert third["batch_id"] is None and third["clip_count"] == 0


def test_pull_empty_inbox(env: Path) -> None:
    assert searcher_pickup.pull_next_batch() == {
        "batch_id": None,
        "clip_count": 0,
        "jobs": [],
    }


def test_consumed_batch_not_repulled_even_if_dir_remains(env: Path) -> None:
    _write_batch(env, "batch_x")
    searcher_pickup.pull_next_batch()
    # Simulate the dir surviving (deletion skipped) but recorded consumed.
    _write_batch(env, "batch_x")  # same id back on disk
    assert searcher_pickup.pull_next_batch()["batch_id"] is None


@pytest.mark.parametrize(
    "kwargs",
    [
        {"schema_version": 2},
        {"producer": "riceposter"},
        {"clips": []},
        {"clips": [{"file": "clip_1.mp4", "position": 0}]},
        {
            "clips": [
                {"file": "clip_1.mp4", "position": 1},
                {"file": "clip_2.mp4", "position": 1},
            ]
        },
        {"clips": [{"file": "../escape.mp4", "position": 1}]},
    ],
)
def test_malformed_manifest_rejected_zero_writes(env: Path, kwargs) -> None:
    _write_batch(env, "batch_bad", **kwargs)
    before = len(jobs._JOBS)
    with pytest.raises(searcher_pickup.PickupError):
        searcher_pickup.pull_next_batch()
    assert len(jobs._JOBS) == before  # no jobs created
    assert "batch_bad" not in searcher_pickup._load_consumed(env)  # not consumed


def test_missing_clip_file_rejected(env: Path) -> None:
    bd = _write_batch(env, "batch_m")
    (bd / "clip_1.mp4").unlink()  # manifest references a now-missing file
    with pytest.raises(searcher_pickup.PickupError):
        searcher_pickup.pull_next_batch()
    assert len(jobs._JOBS) == 0


def test_ingest_failure_rolls_back(env: Path, monkeypatch) -> None:
    _write_batch(
        env,
        "batch_r",
        clips=[
            {"file": "clip_1.mp4", "position": 1},
            {"file": "clip_2.mp4", "position": 2},
        ],
    )
    calls = {"n": 0}

    def flaky(_p):
        calls["n"] += 1
        if calls["n"] == 2:
            raise probe.ProbeError("boom")
        return MediaInfo(width=1080, height=1920, duration=10.0, has_audio=True)

    monkeypatch.setattr(probe, "probe", flaky)
    with pytest.raises(searcher_pickup.PickupError):
        searcher_pickup.pull_next_batch()
    assert jobs._JOBS == {}  # both created jobs rolled back
    assert (env / "batch_r").exists()  # batch left for retry
    assert "batch_r" not in searcher_pickup._load_consumed(env)


def test_endpoint_pulls_over_http(env: Path) -> None:
    _write_batch(env, "batch_http")
    with TestClient(main.app) as client:
        r = client.post("/api/pull-from-searcher")
    assert r.status_code == 200
    assert r.json()["batch_id"] == "batch_http"


def test_endpoint_malformed_is_400(env: Path) -> None:
    _write_batch(env, "batch_e", producer="nope")
    with TestClient(main.app) as client:
        r = client.post("/api/pull-from-searcher")
    assert r.status_code == 400


def test_inbox_default_is_ricesearcher_handoff(monkeypatch) -> None:
    monkeypatch.delenv("RICECLIPPER_SEARCHER_INBOX", raising=False)
    root = searcher_pickup.inbox_root()
    assert root.name == "ricesearcher-handoff"
    assert "riceclipper-handoff" not in str(root)


# --- RiceSuite automatic pull: the read-only inbox (ADR-001 Q12) -------------


def _tree(root: Path) -> list[tuple[str, int]]:
    return sorted(
        (str(p.relative_to(root)), p.stat().st_mtime_ns) for p in root.rglob("*")
    )


def test_waiting_batches_lists_complete_unconsumed_batches_oldest_first(
    env: Path,
) -> None:
    _write_batch(env, "batch_b", created_at="2026-09-05T00:00:00Z")
    _write_batch(env, "batch_a", created_at="2026-09-04T00:00:00Z")
    (env / "batch_c").mkdir()  # no manifest yet: still being written
    (env / "batch_c" / "clip_1.mp4").write_bytes(b"x")
    got = searcher_pickup.waiting_batches()
    assert [b["batch_id"] for b in got] == ["batch_a", "batch_b"]
    assert got[0]["clip_count"] == 1


def test_waiting_batches_excludes_what_was_pulled(env: Path) -> None:
    _write_batch(env, "batch_a", created_at="2026-09-04T00:00:00Z")
    _write_batch(env, "batch_b", created_at="2026-09-05T00:00:00Z")
    searcher_pickup.pull_next_batch()
    assert [b["batch_id"] for b in searcher_pickup.waiting_batches()] == ["batch_b"]


def test_waiting_batches_is_read_only(env: Path) -> None:
    _write_batch(env, "batch_a")
    before = _tree(env)
    searcher_pickup.waiting_batches()
    assert _tree(env) == before
    assert not jobs._JOBS


def test_inbox_endpoint(env: Path) -> None:
    _write_batch(env, "batch_a")
    client = TestClient(main.app)
    r = client.get("/api/searcher-inbox")
    assert r.status_code == 200
    assert [b["batch_id"] for b in r.json()["batches"]] == ["batch_a"]
    assert not jobs._JOBS


def test_inbox_missing_dir_is_empty(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RICECLIPPER_SEARCHER_INBOX", str(tmp_path / "absent"))
    assert searcher_pickup.waiting_batches() == []


# --- open batches: a lost pull reply or a page reload strands nothing -------
# RiceSuite functional audit W1-02: the pull removes the Searcher batch, so the
# page must be able to ask which pulled batch it has not sent or discarded.


def test_a_pulled_batch_stays_open_for_the_page_to_restore(env: Path) -> None:
    _write_batch(env, "b1")
    client = TestClient(main.app)
    pulled = client.post("/api/pull-from-searcher").json()
    # The reply is lost, or the page reloads: the page asks what is open.
    opened = client.get("/api/workspace").json()
    assert opened["batch_id"] == "b1"
    assert opened["clip_count"] == 1
    assert [j["id"] for j in opened["jobs"]] == [j["id"] for j in pulled["jobs"]]
    assert opened["jobs"][0]["title"] == "Ep One"


def test_the_workspace_is_empty_when_nothing_is_open(env: Path) -> None:
    empty = {"batch_id": None, "clip_count": 0, "jobs": []}
    assert TestClient(main.app).get("/api/workspace").json() == empty


def test_a_sent_batch_is_no_longer_open(env: Path, tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RICECLIPPER_HANDOFF_DIR", str(tmp_path / "out"))
    _write_batch(env, "b1")
    client = TestClient(main.app)
    job_id = client.post("/api/pull-from-searcher").json()["jobs"][0]["id"]
    job = jobs.get_job(job_id)
    job.output_path = job.dir / "output.mp4"
    job.output_path.write_bytes(b"rendered")
    sent = client.post(
        "/api/handoff", json={"clips": [{"job_id": job_id, "position": 1}]}
    )
    assert sent.status_code == 200
    assert client.get("/api/workspace").json()["batch_id"] is None


def test_a_discarded_batch_is_no_longer_open(env: Path) -> None:
    _write_batch(env, "b1")
    client = TestClient(main.app)
    client.post("/api/pull-from-searcher")
    other = client.delete("/api/workspace", params={"batch_id": "b0"})
    assert other.json() == {"discarded": False}
    assert client.get("/api/workspace").json()["batch_id"] == "b1"
    done = client.delete("/api/workspace", params={"batch_id": "b1"})
    assert done.json() == {"discarded": True}
    assert client.get("/api/workspace").json()["batch_id"] is None


def test_open_batches_come_back_oldest_first(env: Path) -> None:
    _write_batch(env, "b1", created_at="2026-09-04T00:00:00Z")
    _write_batch(env, "b2", created_at="2026-09-05T00:00:00Z")
    client = TestClient(main.app)
    client.post("/api/pull-from-searcher")
    client.post("/api/pull-from-searcher")
    assert client.get("/api/workspace").json()["batch_id"] == "b1"
    client.delete("/api/workspace", params={"batch_id": "b1"})
    assert client.get("/api/workspace").json()["batch_id"] == "b2"


def test_an_open_batch_whose_jobs_are_gone_is_dropped(env: Path) -> None:
    import shutil

    _write_batch(env, "b1")
    client = TestClient(main.app)
    for j in client.post("/api/pull-from-searcher").json()["jobs"]:
        shutil.rmtree(jobs.WORK_ROOT / j["id"])
    jobs._JOBS.clear()
    assert client.get("/api/workspace").json()["batch_id"] is None
