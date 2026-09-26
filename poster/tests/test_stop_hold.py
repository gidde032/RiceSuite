"""RiceSuite's stop hold (suite ADR-001 Q17, suite SPEC FR-17).

`rice stop` asks Poster for this hold before it signals. While the hold is
set, Poster refuses every new posting run, manual or scheduled, so no run can
start between the stop's idle check and the stop itself. Poster refuses the
hold while a run is active, and the hold ends by itself when its lease ends.
"""

import asyncio
import datetime

from backend import main, run_guard, scheduler
from backend.queue import QueuedBatch, SlotBatch, load_queue, save_queue


def _post_one(client, tmp_media):
    (tmp_media / "A_clip.mp4").write_bytes(b"x")
    return client.post(
        "/api/post",
        json={"slots": [{"slot": "A", "filename": "A_clip.mp4", "caption": "hi",
                         "media_type": "video"}]},
    )


def test_hold_is_refused_while_a_run_is_active(client, monkeypatch):
    monkeypatch.setattr(run_guard, "_post_running", True)
    resp = client.post("/api/stop-hold")
    assert resp.status_code == 200
    assert resp.json() == {"held": False, "active": True}
    assert not run_guard.is_held()


def test_a_held_poster_refuses_a_manual_post(client, tmp_media, monkeypatch):
    posted = []

    async def fake_post_all(slots):
        posted.append(slots)
        return []

    monkeypatch.setattr(main, "POST_MODE", "mock")
    monkeypatch.setattr(main, "post_all_api", fake_post_all)
    assert client.post("/api/stop-hold").json() == {"held": True, "active": False}
    resp = _post_one(client, tmp_media)
    assert resp.status_code == 409
    assert "stopping" in resp.json()["detail"]
    assert posted == []
    assert not run_guard.is_running()


def test_a_held_poster_leaves_a_due_scheduled_batch_pending(client, tmp_path):
    qf = tmp_path / "queue.jsonl"
    batch = QueuedBatch(
        id="due1",
        fire_time=datetime.datetime(2020, 1, 1, tzinfo=datetime.timezone.utc),
        created_at=datetime.datetime.now(datetime.timezone.utc),
        slots=[SlotBatch(slot="A", media_path=str(tmp_path / "a.mp4"), caption="c")],
        status="pending", headless=True,
    )
    save_queue([batch], qf)
    posted = []

    async def fake_post(slots, headless=True, notifier=None):
        posted.append(slots)
        return []

    async def fake_check(slot, platform):
        return "live"

    assert client.post("/api/stop-hold").json()["held"] is True
    asyncio.run(scheduler.execute_batch(
        batch, qf, tmp_path / "media", tmp_path / "history.jsonl",
        object(), fake_check, fake_post,
    ))
    assert posted == []
    assert [b.status for b in load_queue(qf)] == ["pending"]


def test_a_released_hold_allows_posting_again(client):
    client.post("/api/stop-hold")
    assert client.delete("/api/stop-hold").status_code == 200
    assert not run_guard.is_held()
    assert run_guard.try_acquire()


def test_the_hold_ends_when_its_lease_ends(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(run_guard.time, "monotonic", lambda: now[0])
    assert run_guard.hold(60.0)
    assert not run_guard.try_acquire()
    now[0] += 61.0
    assert not run_guard.is_held()
    assert run_guard.try_acquire()
