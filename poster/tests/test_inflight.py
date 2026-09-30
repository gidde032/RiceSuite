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


def test_a_run_that_raises_in_process_is_recorded_unconfirmed(
    tmp_inflight_marker, tmp_history_file, monkeypatch
):
    """An exception mid-run (a browser failure, a bad slot) may come after some
    posts went live. The run must leave unconfirmed rows, not vanish."""
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
    rows = _history(tmp_history_file)
    assert [r["slot"] for r in rows] == ["A"]
    assert outcomes.classify_history_row(rows[0])["instagram"] == "unconfirmed"


def test_a_cancelled_run_is_recorded_unconfirmed(
    tmp_inflight_marker, tmp_history_file, monkeypatch
):
    """A graceful shutdown or supervisor restart cancels the request task."""
    async def cancelled(slots):
        raise asyncio.CancelledError

    monkeypatch.setattr(main, "post_all_api", cancelled)
    monkeypatch.setattr(main, "_validate_active_targets", lambda ids: None)
    media = main.MEDIA_DIR / "inflight-test3.mp4"
    media.write_bytes(b"x")
    try:
        request = main.PostRequest(slots=[{"slot": "A", "filename": media.name, "caption": "c"}])
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(main._run_post(request, True))
    finally:
        media.unlink(missing_ok=True)
    assert [r["slot"] for r in _history(tmp_history_file)] == ["A"]


def test_a_finished_run_keeps_its_marker_when_history_cannot_be_written(
    tmp_inflight_marker, tmp_media, tmp_path, monkeypatch
):
    """W1-03: `_append_history` swallows its own errors. A finished run whose
    rows never reached History must keep its marker, the only evidence that
    its posts may be live, for the next start to record as unconfirmed."""
    from backend.models import PostResult

    async def posted(slots):
        return [PostResult(slot="A", ig_post_id="ig_1", tt_post_id="tt_1")]

    monkeypatch.setattr(main, "post_all_api", posted)
    monkeypatch.setattr(main, "_validate_active_targets", lambda ids: None)
    monkeypatch.setattr(main, "HISTORY_FILE", tmp_path / "no-such-dir" / "history.jsonl")
    (tmp_media / "a.mp4").write_bytes(b"x")
    request = main.PostRequest(slots=[{"slot": "A", "filename": "a.mp4", "caption": "c"}])
    results = asyncio.run(main._run_post(request, True))
    assert [r.slot for r in results] == ["A"]  # the browser still gets its results
    assert tmp_inflight_marker.exists()
    history = tmp_path / "history.jsonl"
    rows = inflight.recover(history)
    assert [r["slot"] for r in rows] == ["A"]
    assert outcomes.classify_history_row(rows[0])["instagram"] == "unconfirmed"


def test_recover_skips_a_malformed_slot_entry(tmp_inflight_marker, tmp_history_file):
    tmp_inflight_marker.write_text(json.dumps(
        {"run_id": "r", "slots": [{"platforms": ["tiktok"]}, {"slot": "B", "platforms": ["tiktok"]}]}
    ))
    rows = inflight.recover(tmp_history_file)
    assert [r["slot"] for r in rows] == ["B"]
    assert not tmp_inflight_marker.exists()


def test_recover_keeps_the_marker_when_history_cannot_be_written(
    tmp_inflight_marker, tmp_path, tmp_history_file
):
    """Startup must not crash-loop, and the evidence must survive for the
    next start."""
    inflight.begin(_slots(tmp_path), "browser", False)
    unwritable = tmp_path / "no-such-dir" / "history.jsonl"
    assert inflight.recover(unwritable) == []
    assert tmp_inflight_marker.exists()


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
    with TestClient(main.app, base_url="http://127.0.0.1:1738"):  # runs the lifespan startup
        pass

    rows = _history(tmp_history_file)
    assert [r["slot"] for r in rows] == ["A", "B"]
    assert all("not retried" in r["interrupted"] for r in rows)
    verdicts = [outcomes.classify_history_row(r) for r in rows]
    assert verdicts[0] == {"instagram": "unconfirmed", "tiktok": "unconfirmed"}
    assert verdicts[1] == {"instagram": "not_attempted", "tiktok": "unconfirmed"}
    assert not tmp_inflight_marker.exists()

    # A second start records nothing more.
    with TestClient(main.app, base_url="http://127.0.0.1:1738"):
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


def test_an_unrecorded_earlier_run_is_recorded_before_a_new_one_starts(
    tmp_inflight_marker, tmp_history_file, tmp_path, monkeypatch
):
    """A marker kept because History could not be written must never be
    overwritten by the next run: record it first."""
    inflight.begin(_slots(tmp_path), "browser", False)  # run 1, unrecorded

    async def fake_post(slots):
        return []

    monkeypatch.setattr(main, "post_all_api", fake_post)
    monkeypatch.setattr(main, "_validate_active_targets", lambda ids: None)
    media = main.MEDIA_DIR / "inflight-test4.mp4"
    media.write_bytes(b"x")
    try:
        request = main.PostRequest(slots=[{"slot": "A", "filename": media.name, "caption": "c"}])
        asyncio.run(main._run_post(request, True))
    finally:
        media.unlink(missing_ok=True)
    rows = _history(tmp_history_file)
    assert [r["slot"] for r in rows if r.get("interrupted")] == ["A", "B"]


def test_a_new_run_is_refused_while_an_earlier_one_cannot_be_recorded(
    tmp_inflight_marker, tmp_path, monkeypatch
):
    from fastapi import HTTPException

    inflight.begin(_slots(tmp_path), "browser", False)
    before = tmp_inflight_marker.read_text()
    monkeypatch.setattr(main, "HISTORY_FILE", tmp_path / "no-such-dir" / "history.jsonl")
    monkeypatch.setattr(main, "_validate_active_targets", lambda ids: None)

    async def never(slots):
        raise AssertionError("must not post while an earlier run is unrecorded")

    monkeypatch.setattr(main, "post_all_api", never)
    media = main.MEDIA_DIR / "inflight-test5.mp4"
    media.write_bytes(b"x")
    try:
        request = main.PostRequest(slots=[{"slot": "A", "filename": media.name, "caption": "c"}])
        with pytest.raises(HTTPException) as exc:
            asyncio.run(main._run_post(request, True))
    finally:
        media.unlink(missing_ok=True)
    assert exc.value.status_code == 409
    assert tmp_inflight_marker.read_text() == before  # evidence intact
