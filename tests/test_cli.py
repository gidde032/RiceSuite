"""`rice` command behaviour, in-process (FR-1, FR-2, FR-4, FR-8, FR-17)."""

import json
import os
import signal
import sys

import pytest

from ricesuite import cli, ports, stopguard
from ricesuite.supervisor import Child, Supervisor

# Kept before the autouse fixture disables it, for the one test that spawns
# harmless sleepers in place of the suite.
REAL_START_ALL = Supervisor.start_all
SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    """Every path a pillar could touch points into tmp_path, and no test here
    may start real processes: `Supervisor.start_all` fails unless a test
    replaces the Supervisor. (A test that reached the real supervisor once
    started all four processes with Searcher on its default data dir.)"""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("RICESUITE_RUN_DIR", str(tmp_path / "run"))
    (tmp_path / "poster-data").mkdir()
    env_file = tmp_path / "ricesuite.env"
    env_file.write_text(
        f"RICESEARCHER_DATA_DIR={tmp_path / 'searcher-data'}\n"
        f"RICESEARCHER_HANDOFF_DIR={tmp_path / 'h1'}\n"
        f"RICECLIPPER_HANDOFF_DIR={tmp_path / 'h2'}\n"
        f"RICEPOSTER_DATA_DIR={tmp_path / 'poster-data'}\n"
        "POST_MODE=mock\n"
        "SCHEDULER_ENABLED=false\n"
    )
    monkeypatch.setenv("RICESUITE_ENV", str(env_file))
    for name in (
        "RICECLIPPER_SEARCHER_INBOX",
        "HANDOFF_DIR",
        "RICESEARCHER_DATA_DIR",
        "RICEPOSTER_DATA_DIR",
        "RICESEARCHER_PROFILES_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    def refuse(self):
        pytest.fail("test_cli must never start real suite processes")

    monkeypatch.setattr(cli.Supervisor, "start_all", refuse)
    monkeypatch.setattr(ports, "startup_conflicts", lambda: [])
    return tmp_path


def _write_state(tmp_path, **children):
    path = cli.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"launcher_pid": os.getpid(), "children": children}))
    return path


@pytest.fixture
def launcher_lock():
    """Hold the launcher lock as a running launcher would."""
    holder = cli.acquire_launcher_lock()
    yield holder
    holder.close()


def test_default_command_is_start():
    assert cli.build_parser().parse_args([]).command is None
    assert cli.build_parser().parse_args(["stop", "--force"]).force


def test_data_plan_refuses_running_old_app(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ports, "startup_conflicts", lambda: ["old app on 1738"])
    assert cli.main(["data", "plan", "--root", str(tmp_path / "new")]) == 1
    assert "old app on 1738" in capsys.readouterr().err


def test_data_plan_refuses_browser_using_source_profile(tmp_path, monkeypatch, capsys):
    class Reply:
        stdout = (
            f"chrome --user-data-dir={tmp_path / 'poster-data/sessions/instagram/A'}"
        )

    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Reply())
    assert cli.main(["data", "plan", "--root", str(tmp_path / "new")]) == 1
    assert "browser is using" in capsys.readouterr().err


def test_data_cli_copy_cutover_and_rollback_on_empty_fixture(
    tmp_path, monkeypatch, capsys
):
    class Reply:
        stdout = ""

    monkeypatch.setattr(cli.subprocess, "run", lambda *args, **kwargs: Reply())
    root = tmp_path / "new"
    for command in ("plan", "copy", "cutover"):
        assert cli.main(["data", command, "--root", str(root)]) == 0
    assert (root / ".cutover.json").is_file()
    assert cli.main(["data", "location"]) == 0
    assert str(root) in capsys.readouterr().out
    assert cli.main(["data", "rollback", "--root", str(root)]) == 0
    assert not (root / ".cutover.json").exists()


def test_status_when_not_running(capsys):
    assert cli.main(["status"]) == 3
    assert "not running" in capsys.readouterr().out


def test_status_lists_children(tmp_path, capsys, launcher_lock):
    _write_state(
        tmp_path, poster={"state": "running", "port": 8793, "pid": 1, "restarts": 2}
    )
    assert cli.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "running" in out and "poster" in out and "restarts 2" in out


def test_state_without_a_pid_is_not_running(tmp_path, capsys):
    cli.state_path().parent.mkdir(parents=True)
    cli.state_path().write_text(json.dumps({"children": {}}))
    assert cli.main(["status"]) == 3
    assert cli.main(["stop"]) == 0
    assert "not running" in capsys.readouterr().out


def test_the_fixture_refuses_a_real_start(capsys):
    """Guard the guard: reaching the real supervisor fails the test."""
    with pytest.raises(pytest.fail.Exception, match="never start real"):
        cli.main(["start"])


def test_start_refuses_on_a_port_conflict(monkeypatch, capsys):
    monkeypatch.setattr(ports, "startup_conflicts", lambda: ["port 1738 is in use"])
    assert cli.main(["start"]) == 1
    assert "refusing to start: port 1738" in capsys.readouterr().err


def test_start_refuses_mismatched_handoff_dirs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HANDOFF_DIR", str(tmp_path / "somewhere-else"))
    assert cli.main(["start"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_start_refuses_while_already_running(tmp_path, capsys):
    holder = cli.acquire_launcher_lock()
    try:
        assert cli.main(["start"]) == 1
    finally:
        holder.close()
    assert "already running" in capsys.readouterr().err


def test_start_warns_about_unknown_keys(tmp_path, monkeypatch, capsys):
    with open(os.environ["RICESUITE_ENV"], "a") as f:
        f.write("POST_MOED=mock\n")
    monkeypatch.setattr(ports, "startup_conflicts", lambda: ["busy"])
    cli.main(["start"])
    assert "POST_MOED" in capsys.readouterr().out


def test_build_children_sets_env_ports_and_gateway_config(tmp_path):
    env = {"RICESEARCHER_HANDOFF_DIR": "/a", "HANDOFF_DIR": "/b"}
    children = {c.name: c for c in cli.build_children(env, tmp_path / "state.json")}
    assert set(children) == {"searcher", "clipper", "poster", "gateway"}
    assert children["poster"].port == ports.PILLAR_PORTS["poster"]
    assert children["poster"].cwd.endswith("poster")
    assert children["clipper"].env["HANDOFF_DIR"] == "/b"
    assert children["searcher"].env["PYTHONUNBUFFERED"] == "1"
    config = json.loads(children["gateway"].env["RICESUITE_GATEWAY_CONFIG"])
    assert config["pillar_ports"] == ports.PILLAR_PORTS
    assert config["state_file"] == str(tmp_path / "state.json")
    assert "RICESUITE_GATEWAY_CONFIG" not in children["poster"].env


def test_pillars_are_told_which_gateway_origin_to_trust(tmp_path, monkeypatch):
    """Each pillar's guard admits state changes from the gateway's origin, so
    it must know the gateway port actually in use (#14, SPEC FR-3)."""
    monkeypatch.setattr(ports, "GATEWAY_PORT", 9123)
    children = {c.name: c for c in cli.build_children({}, tmp_path / "state.json")}
    for name in ("searcher", "clipper", "poster"):
        assert children[name].env["RICESUITE_GATEWAY_PORT"] == "9123"


def test_start_runs_supervises_and_cleans_up(monkeypatch, capsys):
    """The foreground loop: start children, write state, stop on interrupt,
    stop children and remove the state file."""
    events = []

    class FakeSupervisor(Supervisor):
        def start_all(self):
            events.append("start")

        def poll_once(self):
            events.append("poll")

        def stop_all(self, timeout=15.0):
            events.append("stop")

        def snapshot(self):
            return {"poster": {"state": "running"}}

    def fake_sleep(_):
        assert cli.read_state()["children"]["poster"]["state"] == "running"
        raise KeyboardInterrupt

    monkeypatch.setattr(ports, "startup_conflicts", lambda: [])
    monkeypatch.setattr(cli, "Supervisor", FakeSupervisor)
    monkeypatch.setattr(cli.time, "sleep", fake_sleep)
    previous = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        assert cli.main([]) == 0
    finally:
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])
    assert events == ["start", "poll", "stop"]
    assert not cli.state_path().exists()
    assert "stopped" in capsys.readouterr().out


# --- stop -------------------------------------------------------------------


def test_stop_refuses_during_a_posting_run(
    tmp_path, monkeypatch, capsys, launcher_lock
):
    _write_state(tmp_path, poster={"state": "running", "pid": os.getpid(), "port": 1})
    monkeypatch.setattr(
        stopguard,
        "hold_poster",
        lambda port: stopguard.PosterState(reachable=True, active=True),
    )
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: killed.append(sig))
    assert cli.main(["stop"]) == 1
    assert "refusing to stop" in capsys.readouterr().out
    assert signal.SIGTERM not in killed


def test_stop_force_signals_the_launcher(tmp_path, monkeypatch, launcher_lock):
    _write_state(tmp_path, poster={"state": "running", "pid": os.getpid(), "port": 1})
    monkeypatch.setattr(
        stopguard,
        "hold_poster",
        lambda port: stopguard.PosterState(reachable=True, active=True),
    )
    sent = []

    def fake_kill(pid, sig):
        sent.append(sig)
        if sig == signal.SIGTERM:
            launcher_lock.close()  # the launcher exits and its lock is released

    monkeypatch.setattr(cli.os, "kill", fake_kill)
    assert cli.main(["stop", "--force"]) == 0
    assert signal.SIGTERM in sent


def test_stop_decision_skips_poster_when_it_is_not_running(monkeypatch):
    monkeypatch.setattr(
        stopguard,
        "hold_poster",
        lambda port: pytest.fail("must not ask a stopped Poster"),
    )
    state = {"children": {"poster": {"state": "restarting", "pid": None, "port": 1}}}
    assert cli.stop_decision(state, force=False).allowed


# --- Ctrl-C -----------------------------------------------------------------


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_sigterm_stops_immediately():
    handler = cli.InterruptHandler(decide=lambda: pytest.fail("no check on SIGTERM"))
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGTERM)


def test_ctrl_c_stops_when_idle():
    handler = cli.InterruptHandler(decide=lambda: stopguard.StopDecision(True, []))
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT)


def test_ctrl_c_during_a_run_refuses_then_second_press_forces():
    clock, out = Clock(), []
    refuse = stopguard.StopDecision(False, ["refusing to stop: run active"])
    handler = cli.InterruptHandler(decide=lambda: refuse, clock=clock, out=out.append)
    handler(signal.SIGINT)  # refused, no exception
    assert any("again within" in line for line in out)
    clock.now += 3
    with pytest.raises(KeyboardInterrupt):
        handler(signal.SIGINT)


def test_second_ctrl_c_after_the_window_is_checked_again():
    clock = Clock()
    refuse = stopguard.StopDecision(False, [])
    handler = cli.InterruptHandler(
        decide=lambda: refuse, clock=clock, out=lambda _: None
    )
    handler(signal.SIGINT)
    clock.now += cli.FORCE_WINDOW_S + 1
    handler(signal.SIGINT)  # still refused, not forced


def test_module_entry_point_runs_the_cli(tmp_path):
    import subprocess

    env = dict(os.environ, RICESUITE_RUN_DIR=str(tmp_path / "r"))
    result = subprocess.run(
        [sys.executable, "-m", "ricesuite", "status"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 3 and "not running" in result.stdout


# --- launcher identity and leftovers (stale state, pid reuse, SIGKILL) -------


def test_stale_state_with_a_reused_pid_is_not_running_and_is_never_signalled(
    tmp_path, monkeypatch, capsys
):
    """The recorded launcher pid now belongs to an unrelated live process
    (here: this test). Without the launcher's lock it is not RiceSuite."""
    cli.state_path().parent.mkdir(parents=True)
    cli.state_path().write_text(
        json.dumps({"launcher_pid": os.getpid(), "children": {}})
    )
    sent = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert cli.main(["status"]) == 3
    assert cli.main(["stop"]) == 0
    assert "not running" in capsys.readouterr().out
    assert (os.getpid(), signal.SIGTERM) not in sent


def test_a_held_launcher_lock_means_running(tmp_path):
    holder = cli.acquire_launcher_lock()
    try:
        assert holder is not None
        assert cli.launcher_running()
        assert cli.acquire_launcher_lock() is None  # a second launcher is refused
    finally:
        holder.close()
    assert not cli.launcher_running()


def _fake_child(target, port=1):
    """A live process whose command line looks like a suite child. A waiter
    thread reaps it the moment it exits, as launchd/init would for a real
    leftover whose launcher died (an unreaped zombie still looks alive)."""
    import subprocess
    import threading

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "uvicorn",
            target,
            "--port",
            str(port),
        ],
        start_new_session=True,
    )
    threading.Thread(target=proc.wait, daemon=True).start()
    return proc


def test_leftover_children_are_reported_and_block_a_new_start(
    tmp_path, monkeypatch, capsys
):
    child = _fake_child("backend.main:app")
    try:
        cli.state_path().parent.mkdir(parents=True)
        cli.state_path().write_text(
            json.dumps(
                {
                    "launcher_pid": 999999,
                    "children": {
                        "poster": {"state": "running", "pid": child.pid, "port": 1}
                    },
                }
            )
        )
        assert cli.main(["status"]) == 4
        assert f"pid {child.pid}" in capsys.readouterr().out
        monkeypatch.setattr(ports, "startup_conflicts", lambda: [])
        assert cli.main(["start"]) == 1
        assert "still alive" in capsys.readouterr().err
    finally:
        child.kill()
        child.wait()


def test_stop_guards_then_terminates_leftovers(tmp_path, capsys):
    child = _fake_child("backend.main:app")
    try:
        cli.state_path().parent.mkdir(parents=True)
        cli.state_path().write_text(
            json.dumps(
                {
                    "launcher_pid": 999999,
                    "children": {
                        "poster": {"state": "running", "pid": child.pid, "port": 1}
                    },
                }
            )
        )
        # Poster's port does not answer, so a run may be active: refuse.
        assert cli.main(["stop"]) == 1
        assert child.poll() is None
        assert cli.main(["stop", "--force"]) == 0
        assert child.wait(timeout=20) is not None
        assert not cli.state_path().exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


def test_a_live_pid_that_is_not_a_suite_child_is_not_a_leftover(tmp_path):
    state = {
        "children": {"poster": {"state": "running", "pid": os.getpid(), "port": 1}}
    }
    assert cli.leftovers(state) == []


def test_status_while_the_launcher_is_starting(tmp_path, capsys, launcher_lock):
    """Lock held, state not written yet: starting, not 'not running'."""
    assert cli.main(["status"]) == 0
    assert "starting" in capsys.readouterr().out


def test_a_suite_target_on_another_port_is_not_a_leftover(tmp_path):
    """A developer's own `uvicorn backend.main:app --port 1738` must never be
    taken for a suite child just because a stale state file names its pid."""
    import subprocess
    import threading

    proc = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import time; time.sleep(60)",
            "uvicorn",
            "backend.main:app",
            "--port",
            "1738",
        ],
        start_new_session=True,
    )
    threading.Thread(target=proc.wait, daemon=True).start()
    try:
        state = {
            "children": {"poster": {"state": "running", "pid": proc.pid, "port": 8793}}
        }
        assert cli.leftovers(state) == []
        state["children"]["poster"]["port"] = 1738
        assert [c["pid"] for c in cli.leftovers(state)] == [proc.pid]
    finally:
        proc.kill()


def test_stop_keeps_the_record_when_a_leftover_survives(tmp_path, monkeypatch, capsys):
    child = _fake_child("backend.main:app")
    try:
        cli.state_path().parent.mkdir(parents=True)
        cli.state_path().write_text(
            json.dumps(
                {
                    "launcher_pid": 999999,
                    "children": {
                        "poster": {"state": "running", "pid": child.pid, "port": 1}
                    },
                }
            )
        )
        monkeypatch.setattr(cli, "_terminate", lambda found, timeout=15.0: None)
        assert cli.main(["stop", "--force"]) == 1
        assert cli.state_path().exists()
        assert "still alive" in capsys.readouterr().out
    finally:
        child.kill()


def test_process_args_survive_a_long_interpreter_path(tmp_path):
    """CI's interpreter lives under a long path; the arguments after it must
    still be visible (a plain piped `ps` truncates at 80 columns on Linux)."""
    import subprocess
    import threading

    deep = tmp_path / ("d" * 60) / ("e" * 60)
    deep.mkdir(parents=True)
    link = deep / "python"
    link.symlink_to(sys.executable)
    proc = subprocess.Popen(
        [str(link), "-c", "import time; time.sleep(30)", "uvicorn", "app.main:app"],
        start_new_session=True,
    )
    threading.Thread(target=proc.wait, daemon=True).start()
    try:
        import time

        for _ in range(50):
            args = cli.process_args(proc.pid)
            if "app.main:app" in args:
                break
            time.sleep(0.05)
        assert "uvicorn" in args and "app.main:app" in args
    finally:
        proc.kill()


# --- state publication (W2-02) and a stale state during startup -------------


def _dead_pid():
    import subprocess

    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_state_names_each_child_as_soon_as_it_is_spawned(tmp_path, monkeypatch):
    """A launcher killed right after it spawned Poster must still leave a state
    that names Poster, so `rice status` and a guarded `rice stop` find it."""
    monkeypatch.setattr(cli.Supervisor, "start_all", REAL_START_ALL)
    monkeypatch.setattr(
        cli,
        "build_children",
        lambda environ, state_file: [
            Child(name=n, argv=SLEEPER, cwd=str(tmp_path), env=dict(os.environ), port=p)
            for n, p in (("searcher", 1), ("clipper", 2), ("poster", 3), ("gateway", 4))
        ],
    )
    seen = []
    real_spawn = Supervisor._spawn

    def spawn_then_die(self, child):
        real_spawn(self, child)
        if child.name == "poster":
            seen.append(cli.read_state())
            raise KeyboardInterrupt  # the launcher is gone from here on

    monkeypatch.setattr(Supervisor, "_spawn", spawn_then_die)
    previous = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        assert cli.main([]) == 0
    finally:
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])
    [state] = seen
    assert state is not None, "Poster ran with no state naming it"
    assert state["launcher_pid"] == os.getpid()
    assert state["children"]["poster"]["pid"]
    assert state["children"]["poster"]["state"] == "running"


def test_start_removes_a_stale_state_before_it_spawns(tmp_path, monkeypatch):
    """While a new launcher starts, a state left by an earlier one must not
    offer `rice stop` the old launcher pid to signal."""
    stale = _write_state(tmp_path)
    stale.write_text(json.dumps({"launcher_pid": _dead_pid(), "children": {}}))
    seen = []

    class FakeSupervisor(Supervisor):
        def start_all(self):
            seen.append(cli.read_state())
            raise KeyboardInterrupt

        def stop_all(self, timeout=15.0):
            pass

    monkeypatch.setattr(cli, "Supervisor", FakeSupervisor)
    previous = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    try:
        assert cli.main([]) == 0
    finally:
        signal.signal(signal.SIGINT, previous[0])
        signal.signal(signal.SIGTERM, previous[1])
    assert seen == [None]


def test_stop_with_a_dead_recorded_launcher_pid_does_not_crash(
    tmp_path, capsys, launcher_lock
):
    path = _write_state(tmp_path)
    path.write_text(json.dumps({"launcher_pid": _dead_pid(), "children": {}}))
    assert cli.main(["stop"]) == 1
    assert "try again" in capsys.readouterr().err


# --- a stop that does not happen releases Poster's stop hold (review S-2) ----


def _held(monkeypatch):
    monkeypatch.setattr(
        stopguard,
        "hold_poster",
        lambda port: stopguard.PosterState(reachable=True, held=True),
    )
    released = []
    monkeypatch.setattr(stopguard, "release_poster", lambda port: released.append(port))
    return released


def test_a_stop_that_cannot_signal_the_launcher_releases_the_hold(
    tmp_path, monkeypatch, capsys, launcher_lock
):
    path = _write_state(
        tmp_path, poster={"state": "running", "pid": os.getpid(), "port": 7}
    )
    state = json.loads(path.read_text())
    state["launcher_pid"] = _dead_pid()
    path.write_text(json.dumps(state))
    released = _held(monkeypatch)
    assert cli.main(["stop"]) == 1
    assert released == [7]


def test_leftovers_that_survive_the_stop_release_the_hold(
    tmp_path, monkeypatch, capsys
):
    child = _fake_child("backend.main:app", port=7)
    try:
        cli.state_path().parent.mkdir(parents=True)
        cli.state_path().write_text(
            json.dumps(
                {
                    "launcher_pid": 999999,
                    "children": {
                        "poster": {"state": "running", "pid": child.pid, "port": 7}
                    },
                }
            )
        )
        released = _held(monkeypatch)
        monkeypatch.setattr(cli, "_terminate", lambda found, timeout=15.0: None)
        assert cli.main(["stop"]) == 1
        assert released == [7]
    finally:
        child.kill()
