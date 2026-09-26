"""`rice stop` refuses during a posting run and warns about due batches
(ADR-001 Q17, SPEC FR-17/FR-18). Poster is played by a loopback fake."""

import datetime as dt
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ricesuite import stopguard


class FakePoster:
    def __init__(self, progress, batches):
        self.progress, self.batches = progress, batches
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = {
                    "/api/post-progress": outer.progress,
                    "/api/queue": {"batches": outer.batches},
                }.get(self.path)
                data = json.dumps(body).encode()
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def poster():
    fakes = []

    def make(progress=None, batches=()):
        fake = FakePoster(progress or {"active": False}, list(batches))
        fakes.append(fake)
        return fake

    yield make
    for fake in fakes:
        fake.close()


def _at(minutes):
    return (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)).isoformat()


def test_idle_poster_allows_the_stop(poster):
    state = stopguard.read_poster_state(poster().port)
    assert state.reachable and not state.active
    decision = stopguard.decide(state, poster_running=True, force=False)
    assert decision.allowed and decision.messages == []


def test_active_manual_run_refuses_without_force(poster):
    state = stopguard.read_poster_state(poster({"active": True}).port)
    decision = stopguard.decide(state, poster_running=True, force=False)
    assert not decision.allowed
    assert "posting run is in progress" in decision.messages[-1]
    assert "--force" in decision.messages[-1]


def test_running_scheduled_batch_counts_as_active(poster):
    batches = [{"id": "b1", "status": "running", "fire_time": _at(-1)}]
    state = stopguard.read_poster_state(poster(batches=batches).port)
    assert state.active
    assert not stopguard.decide(state, True, force=False).allowed


def test_force_stops_anyway_and_says_so(poster):
    state = stopguard.read_poster_state(poster({"active": True}).port)
    decision = stopguard.decide(state, True, force=True)
    assert decision.allowed
    assert decision.messages[-1].startswith("--force")


def test_unreachable_running_poster_is_treated_as_possibly_posting():
    dead = stopguard.read_poster_state(1, timeout=0.5)
    assert not dead.reachable
    assert not stopguard.decide(dead, poster_running=True, force=False).allowed
    assert stopguard.decide(dead, poster_running=True, force=True).allowed


def test_stopped_poster_does_not_block():
    assert stopguard.decide(None, poster_running=False, force=False).allowed


def test_due_and_overdue_batches_warn_but_do_not_block(poster):
    batches = [
        {"id": "overdue-1", "status": "pending", "fire_time": _at(-5)},
        {"id": "soon-0002", "status": "pending", "fire_time": _at(20)},
        {"id": "later-003", "status": "pending", "fire_time": _at(90)},
        {"id": "interrupt", "status": "interrupted", "fire_time": _at(-60)},
    ]
    state = stopguard.read_poster_state(poster(batches=batches).port)
    assert [b["id"] for b in state.due] == ["overdue-1", "soon-0002"]
    decision = stopguard.decide(state, True, force=False)
    assert decision.allowed
    assert len(decision.messages) == 2
    assert all("next start" in m for m in decision.messages)


def test_naive_fire_times_are_read_as_utc(poster):
    naive = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10)).replace(tzinfo=None)
    batches = [{"id": "n", "status": "pending", "fire_time": naive.isoformat()}]
    state = stopguard.read_poster_state(poster(batches=batches).port)
    assert len(state.due) == 1


def test_malformed_fire_time_is_skipped(poster):
    batches = [{"id": "x", "status": "pending", "fire_time": "not a time"}]
    state = stopguard.read_poster_state(poster(batches=batches).port)
    assert state.due == []
