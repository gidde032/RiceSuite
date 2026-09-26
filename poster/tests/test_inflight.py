"""A post in flight at crash time is recorded unconfirmed, never retried.

RiceSuite ADR-001 Q17 / suite SPEC FR-16. Manual Post All runs use the
in-flight marker (backend/inflight.py); scheduled batches use the existing
running → interrupted startup sweep. Both are pinned here.
"""

import asyncio
import datetime
import json

import pytest
from fastapi.testclient import TestClient

from backend import inflight, main, outcomes
from backend import queue as queue_mod
from backend import scheduler


def _slots(tmp_path):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"x")
    return [
        {"slot": "A", "media_path": media, "caption": "c1",
         "enabled_platforms": {"instagram", "tiktok"}},
        {"slot": "B", "media_path": media, "caption": "c2",
         "enabled_platforms": {"tiktok"}},
    ]


def _history(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_marker_is_written_before_posting_and_removed_after(
    tmp_inflight_marker, tmp_path, monkeypatch
):
    seen = {}

    async def fake_post(slots):
        seen["marker_during_run"] = tmp_inflight_marker.exists()
        return []

    monkeypatch.setattr(main, "post_all_api", fake_post)
    monkeypatch.setattr(main, "_validate_active_targets", lambda ids: None)
    media = main.MEDIA_DIR / "inflight-test.mp4"
    media.write_bytes(b"x")
    try:
        request = main.PostRequest(slots=[{"slot": "A", "filename": media.name, "caption": "c"}])
        asyncio.run(main._run_post(request, True))
    finally:
        media.unlink(missing_ok=True)
    assert seen["marker_during_run"] is True
    assert not tmp_inflight_marker.exists()


def test_marker_is_removed_when_the_run_raises_in_process(
    tmp_inflight_marker, monkeypatch
):
    async def boom(slots):
        raise RuntimeError("browser failed")

    monkeypatch.setattr(main, "post_all_api", boom)
    monkeypatch.setattr(main, "_validate_active_targets", lambda ids: None)
    media = main.MEDIA_DIR / "inflight-test2.mp4"
    media.write_bytes(b"x")
    try:
        request = main.PostRequest(slots=[{"slot": "A", "filename": media.name, "caption": "c"}])
        with pytest.raises(RuntimeError):
            asyncio.run(main._run_post(request, True))
    finally:
        media.unlink(missing_ok=True)
    assert not tmp_inflight_marker.exists()


def test_crash_mid_run_is_recorded_unconfirmed_on_next_start_and_not_retried(
    tmp_inflight_marker, tmp_history_file, tmp_path, monkeypatch
):
    # The process "died" after begin(): the marker survives, end() never ran.
    inflight.begin(_slots(tmp_path), "browser", False)
    assert tmp_inflight_marker.exists()

    def never(*args, **kwargs):
        raise AssertionError("a crashed run must never be retried")

    monkeypatch.setattr(main, "post_all_api", never)
    monkeypatch.setattr(main, "post_all_browser", never)
    monkeypatch.setattr(main, "SCHEDULER_ENABLED", False)
    with TestClient(main.app):  # runs the lifespan startup
        pass

    rows = _history(tmp_history_file)
    assert [r["slot"] for r in rows] == ["A", "B"]
    assert all("not retried" in r["interrupted"] for r in rows)
    verdicts = [outcomes.classify_history_row(r) for r in rows]
    assert verdicts[0] == {"instagram": "unconfirmed", "tiktok": "unconfirmed"}
    assert verdicts[1] == {"instagram": "not_attempted", "tiktok": "unconfirmed"}
    assert not tmp_inflight_marker.exists()

    # A second start records nothing more.
    with TestClient(main.app):
        pass
    assert len(_history(tmp_history_file)) == 2


def test_clean_start_records_nothing(tmp_history_file, tmp_inflight_marker):
    assert inflight.recover(tmp_history_file) == []
    assert _history(tmp_history_file) == []


def test_unreadable_marker_is_set_aside_not_fatal(tmp_inflight_marker, tmp_history_file):
    tmp_inflight_marker.write_text("{not json")
    assert inflight.recover(tmp_history_file) == []
    assert not tmp_inflight_marker.exists()
    assert tmp_inflight_marker.with_name(tmp_inflight_marker.name + ".corrupt").exists()


def test_scheduled_batch_running_at_crash_becomes_interrupted_never_rerun(tmp_path):
    """The scheduled half of Q17 is RicePoster's existing startup sweep."""
    qf = tmp_path / "queue.jsonl"
    now = datetime.datetime.now(datetime.timezone.utc)
    batch = queue_mod.QueuedBatch(
        id="crashed1", fire_time=now - datetime.timedelta(minutes=5),
        created_at=now - datetime.timedelta(hours=1),
        slots=[queue_mod.SlotBatch(slot="A", media_path=str(tmp_path / "m.mp4"),
                                   caption="c")],
        status="running", headless=True,
    )
    qf.write_text(json.dumps(batch.to_dict()) + "\n")
    overdue = asyncio.run(scheduler.startup_sweep(qf, None))
    assert overdue == []  # never handed back for execution
    (reloaded,) = queue_mod.load_queue(qf)
    assert reloaded.status == "interrupted"
