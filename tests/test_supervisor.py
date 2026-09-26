"""Supervisor: crash restarts with backoff, isolation, clean stop (FR-5, Q17)."""

import os
import sys
import time

from ricesuite.supervisor import Child, Supervisor

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]
CRASHER = [sys.executable, "-c", "raise SystemExit(3)"]


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _child(name, argv, tmp_path):
    return Child(name=name, argv=argv, cwd=str(tmp_path), env=dict(os.environ), port=0)


def _wait_exit(child, timeout=10):
    deadline = time.monotonic() + timeout
    while child.proc.poll() is None and time.monotonic() < deadline:
        time.sleep(0.02)


def test_crashed_child_restarts_with_backoff_and_only_it_restarts(tmp_path):
    clock = FakeClock()
    logs = []
    crasher = _child("clipper", CRASHER, tmp_path)
    steady = _child("poster", SLEEPER, tmp_path)
    sup = Supervisor([crasher, steady], log=logs.append, clock=clock, backoff=(1, 5))
    try:
        sup.start_all()
        _wait_exit(crasher)
        sup.poll_once()
        assert crasher.state == "restarting"
        assert crasher.last_exit == 3
        clock.now += 0.5
        sup.poll_once()
        assert crasher.restarts == 0  # backoff not yet elapsed
        clock.now += 0.6
        sup.poll_once()
        assert crasher.restarts == 1
        _wait_exit(crasher)
        sup.poll_once()
        assert crasher.restart_at == clock.now + 5  # second quick crash: next step
        clock.now += 5
        sup.poll_once()
        assert crasher.restarts == 2
        assert steady.restarts == 0 and steady.state == "running"
        assert any("restarting in 1s" in line for line in logs)
    finally:
        sup.stop_all(timeout=5)


def test_backoff_resets_after_a_stable_run(tmp_path):
    clock = FakeClock()
    child = _child("searcher", CRASHER, tmp_path)
    sup = Supervisor([child], log=lambda _: None, clock=clock, backoff=(1, 5, 30))
    try:
        sup.start_all()
        child.streak = 2
        _wait_exit(child)
        clock.now += 120  # it had been up for two minutes
        sup.poll_once()
        assert child.restart_at == clock.now + 1
    finally:
        sup.stop_all(timeout=5)


def test_children_run_in_their_own_session(tmp_path):
    """A terminal Ctrl-C must reach only the launcher, never a pillar mid-post."""
    child = _child("poster", SLEEPER, tmp_path)
    sup = Supervisor([child], log=lambda _: None)
    try:
        sup.start_all()
        assert os.getsid(child.proc.pid) == child.proc.pid
        assert os.getsid(child.proc.pid) != os.getsid(0)
    finally:
        sup.stop_all(timeout=5)


def test_stop_all_terminates_and_disables_restarts(tmp_path):
    children = [_child(n, SLEEPER, tmp_path) for n in ("a", "b")]
    sup = Supervisor(children, log=lambda _: None)
    sup.start_all()
    sup.stop_all(timeout=5)
    assert all(c.proc.poll() is not None for c in children)
    sup.poll_once()
    assert all(c.restarts == 0 and c.state == "stopped" for c in children)


def test_stop_all_kills_a_child_that_ignores_sigterm(tmp_path):
    stubborn = [
        sys.executable,
        "-c",
        "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)",
    ]
    child = _child("x", stubborn, tmp_path)
    logs = []
    sup = Supervisor([child], log=logs.append)
    sup.start_all()
    time.sleep(0.5)  # let it install the handler
    sup.stop_all(timeout=0.5)
    assert child.proc.poll() is not None
    assert any("killing" in line for line in logs)


def test_snapshot_reports_state_pid_port_and_restarts(tmp_path):
    child = _child("clipper", SLEEPER, tmp_path)
    child.port = 8792
    sup = Supervisor([child], log=lambda _: None)
    try:
        sup.start_all()
        snap = sup.snapshot()["clipper"]
        assert snap["state"] == "running"
        assert snap["pid"] == child.proc.pid
        assert snap["port"] == 8792 and snap["restarts"] == 0
    finally:
        sup.stop_all(timeout=5)
