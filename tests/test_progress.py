"""Operation observation must preserve identity and commit boundaries."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from ricesuite.progress import Progress, ProgressStore, notify


def test_intermediate_items_and_publication_are_not_completion():
    store = ProgressStore()
    run = store.start("attempt-a", "handoff", "profile-a")
    notify(
        run,
        "items",
        items=[{"id": "one", "title": "First"}, {"id": "two", "title": "Second"}],
    )
    notify(run, "preparing", item_id="one")
    first = store.get("attempt-a")
    assert first["current"]["id"] == "one"
    assert first["completed"] == 0
    notify(run, "prepared", item_id="one")
    notify(run, "preparing", item_id="two")
    snapshot = store.get("attempt-a")
    assert snapshot["status"] == "active"
    assert snapshot["completed"] == 1
    assert snapshot["total"] == 2
    assert snapshot["current"]["id"] == "two"
    assert snapshot["scope"] == "profile-a"
    notify(run, "prepared", item_id="two")
    notify(run, "committing")
    assert store.get("attempt-a")["current"] is None
    assert not store.get("attempt-a")["published"]
    notify(run, "published", batch_id="batch-a")
    assert store.get("attempt-a")["status"] == "active"
    assert store.get("attempt-a")["published"]
    run.finish(batch_id="batch-a", detail="Confirmed")
    final = store.get("attempt-a")
    assert final["status"] == "complete"
    assert final["completed"] == 2
    assert all(it["state"] == "complete" for it in final["items"])
    assert final["batch_id"] == "batch-a"


def test_failed_after_publish_is_unconfirmed_and_terminal_cannot_be_rewritten():
    run = Progress("a", "send")
    run.notify("published", batch_id="batch-written")
    run.finish("failed", "Receipt write failed")
    run.notify("copying", item_id="one")
    run.finish("complete", batch_id="other")
    snap = run.snapshot()
    assert snap["status"] == "unconfirmed"
    assert snap["detail"] == "Receipt write failed"
    assert snap["batch_id"] == "batch-written"


def test_snapshot_is_detached_and_attempts_cannot_overwrite_one_another():
    store = ProgressStore()
    first = store.start("first", "pull")
    first.notify("items", items=[{"id": "clip", "position": 9}])
    second = store.start("second", "send")
    second.finish("failed", "No output")
    snapshot = store.get("first")
    snapshot["items"][0]["state"] = "complete"
    snapshot["status"] = "complete"
    assert store.get("first")["items"][0]["state"] == "waiting"
    assert store.get("first")["status"] == "active"
    assert store.get("second")["status"] == "failed"
    assert store.get("missing") is None
    with pytest.raises(ValueError, match="already exists"):
        store.start("first", "other")
    assert store.get("first")["operation"] == "pull"


def test_bounded_store_prefers_evicting_completed_records():
    store = ProgressStore(limit=2)
    store.start("active", "pull")
    store.start("old-result", "send").finish()
    store.start("new", "send")
    assert store.get("old-result") is None
    assert store.get("active") is not None
    store.start("another", "send")
    assert store.get("active") is None
    assert store.get("new") is not None
    with pytest.raises(ValueError, match="positive"):
        ProgressStore(0)


def test_observer_exception_and_unknown_item_do_not_fail_work(monkeypatch):
    run = Progress("a", "handoff")
    run.notify("preparing", item_id="unknown")
    assert run.snapshot()["current"] is None

    def broken(*args, **kwargs):
        raise RuntimeError("observation failed")

    monkeypatch.setattr(run, "notify", broken)
    notify(run, "prepared", item_id="one")
    notify(None, "prepared", item_id="one")
    run.finish()
    assert run.snapshot()["status"] == "complete"


def test_progress_reads_and_other_operations_do_not_share_mutable_records():
    store = ProgressStore()
    run = store.start("writer", "pull")
    run.notify("items", items=[{"id": str(i)} for i in range(10)])
    with ThreadPoolExecutor(max_workers=3) as pool:
        reads = [pool.submit(store.get, "writer") for _ in range(20)]
        for i in range(10):
            notify(run, "importing", item_id=str(i))
            notify(run, "imported", item_id=str(i))
        for future in reads:
            snapshot = future.result()
            assert snapshot["total"] == 10
            assert 0 <= snapshot["completed"] <= 10
    assert store.get("writer")["completed"] == 10
