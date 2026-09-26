"""`rice stop` refuses during a posting run and warns about due batches
(ADR-001 Q17, SPEC FR-17/FR-18). Poster is played by a loopback fake."""

import datetime as dt
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ricesuite import cli, stopguard


class FakePoster:
    """Poster's stop-hold and queue API on loopback. ``routes`` replaces the
    answer for a (method, path) with (status, JSON body). ``try_start_run``
    starts a posting run the way Poster's run guard does: never while the stop
    hold is set."""

    def __init__(self, progress, batches, routes=None):
        self.progress, self.batches = progress, batches
        self.routes = routes or {}
        self.requests: list[tuple[str, str]] = []
        self.held = False
        self.run_started = False
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _answer(self, method):
                outer.requests.append((method, self.path))
                status, body = outer.routes.get((method, self.path)) or outer.answer(
                    method, self.path
                )
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._answer("GET")

            def do_POST(self):
                self._answer("POST")

            def do_DELETE(self):
                self._answer("DELETE")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_port
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def answer(self, method, path):
        if (method, path) == ("GET", "/api/queue"):
            return 200, {"batches": self.batches}
        if (method, path) == ("POST", "/api/stop-hold"):
            active = bool(self.progress.get("active")) or self.run_started
            self.held = not active
            return 200, {"held": self.held, "active": active}
        if (method, path) == ("DELETE", "/api/stop-hold"):
            self.held = False
            return 200, {"held": False}
        return 404, {"detail": "Not Found"}

    def try_start_run(self) -> bool:
        if self.held or self.run_started:
            return False
        self.run_started = True
        return True

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def poster():
    fakes = []

    def make(progress=None, batches=(), routes=None):
        fake = FakePoster(progress or {"active": False}, list(batches), routes)
        fakes.append(fake)
        return fake

    yield make
    for fake in fakes:
        fake.close()


def _running(port):
    """Launcher state in which Poster runs (this test process stands in for
    its pid) and listens on ``port``."""
    return {
        "children": {"poster": {"state": "running", "pid": os.getpid(), "port": port}}
    }


# --- W1-04: an answer that cannot confirm "idle" refuses the stop -----------


@pytest.mark.parametrize(
    "routes",
    [
        {("GET", "/api/queue"): (500, {"detail": "queue unreadable"})},
        {("POST", "/api/stop-hold"): (500, {"detail": "boom"})},
        {("GET", "/api/queue"): (200, [])},
        {("GET", "/api/queue"): (200, {"batches": "none"})},
        {("POST", "/api/stop-hold"): (200, {})},
    ],
    ids=["queue-500", "hold-500", "list-body", "bad-batches", "no-fields"],
)
def test_an_unexpected_poster_answer_refuses_an_unforced_stop(poster, routes):
    fake = poster(routes=routes)
    decision = cli.stop_decision(_running(fake.port), force=False)
    assert not decision.allowed
    assert "may be active" in decision.messages[-1]


# --- W1-05: no run can start between the stop check and the signal ---------


def test_a_run_cannot_start_between_the_stop_check_and_the_signal(poster):
    fake = poster()
    assert cli.stop_decision(_running(fake.port), force=False).allowed
    # A manual Post All or a due scheduled batch tries to start now, after the
    # check and before the launcher receives SIGTERM.
    assert not fake.try_start_run()


def _at(minutes):
    return (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=minutes)).isoformat()


def test_idle_poster_allows_the_stop(poster):
    state = stopguard.hold_poster(poster().port)
    assert state.reachable and not state.active
    decision = stopguard.decide(state, poster_running=True, force=False)
    assert decision.allowed and decision.messages == []


def test_active_manual_run_refuses_without_force(poster):
    state = stopguard.hold_poster(poster({"active": True}).port)
    decision = stopguard.decide(state, poster_running=True, force=False)
    assert not decision.allowed
    assert "posting run is in progress" in decision.messages[-1]
    assert "--force" in decision.messages[-1]


def test_running_scheduled_batch_counts_as_active(poster):
    batches = [{"id": "b1", "status": "running", "fire_time": _at(-1)}]
    state = stopguard.hold_poster(poster(batches=batches).port)
    assert state.active
    assert not stopguard.decide(state, True, force=False).allowed


def test_force_stops_anyway_and_says_so(poster):
    state = stopguard.hold_poster(poster({"active": True}).port)
    decision = stopguard.decide(state, True, force=True)
    assert decision.allowed
    assert decision.messages[-1].startswith("--force")


def test_unreachable_running_poster_is_treated_as_possibly_posting():
    dead = stopguard.hold_poster(1, timeout=0.5)
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
    state = stopguard.hold_poster(poster(batches=batches).port)
    assert [b["id"] for b in state.due] == ["overdue-1", "soon-0002"]
    decision = stopguard.decide(state, True, force=False)
    assert decision.allowed
    assert len(decision.messages) == 2
    assert all("next start" in m for m in decision.messages)


def test_naive_fire_times_are_read_as_utc(poster):
    naive = (dt.datetime.now(dt.UTC) + dt.timedelta(minutes=10)).replace(tzinfo=None)
    batches = [{"id": "n", "status": "pending", "fire_time": naive.isoformat()}]
    state = stopguard.hold_poster(poster(batches=batches).port)
    assert len(state.due) == 1


def test_malformed_fire_time_is_skipped(poster):
    batches = [{"id": "x", "status": "pending", "fire_time": "not a time"}]
    state = stopguard.hold_poster(poster(batches=batches).port)
    assert state.due == []


def test_a_refused_stop_releases_the_hold(poster):
    batches = [{"id": "b1", "status": "running", "fire_time": _at(-1)}]
    fake = poster(batches=batches)
    assert not cli.stop_decision(_running(fake.port), force=False).allowed
    assert ("DELETE", "/api/stop-hold") in fake.requests
    assert not fake.held


def test_an_unreadable_queue_releases_the_hold(poster):
    fake = poster(routes={("GET", "/api/queue"): (500, {"detail": "boom"})})
    assert not stopguard.hold_poster(fake.port).reachable
    assert ("DELETE", "/api/stop-hold") in fake.requests
    assert not fake.held


def test_a_busy_poster_refuses_the_hold(poster):
    fake = poster({"active": True})
    state = stopguard.hold_poster(fake.port)
    assert state.reachable and state.active and not state.held
    assert ("DELETE", "/api/stop-hold") not in fake.requests
