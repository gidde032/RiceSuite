"""`rice` command behaviour, in-process (FR-1, FR-2, FR-4, FR-8, FR-17)."""

import json
import os
import signal
import sys

import pytest

from ricesuite import cli, ports, stopguard
from ricesuite.supervisor import Supervisor


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("RICESUITE_RUN_DIR", str(tmp_path / "run"))
    env_file = tmp_path / "ricesuite.env"
    env_file.write_text(
        f"RICESEARCHER_HANDOFF_DIR={tmp_path / 'h1'}\n"
        f"RICECLIPPER_HANDOFF_DIR={tmp_path / 'h2'}\n"
    )
    monkeypatch.setenv("RICESUITE_ENV", str(env_file))
    for name in ("RICECLIPPER_SEARCHER_INBOX", "HANDOFF_DIR"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path


def _write_state(tmp_path, **children):
    path = cli.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"launcher_pid": os.getpid(), "children": children}))
    return path


def test_default_command_is_start():
    assert cli.build_parser().parse_args([]).command is None
    assert cli.build_parser().parse_args(["stop", "--force"]).force


def test_status_when_not_running(capsys):
    assert cli.main(["status"]) == 3
    assert "not running" in capsys.readouterr().out


def test_status_lists_children(tmp_path, capsys):
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


def test_start_refuses_on_a_port_conflict(monkeypatch, capsys):
    monkeypatch.setattr(ports, "startup_conflicts", lambda: ["port 1738 is in use"])
    assert cli.main(["start"]) == 1
    assert "refusing to start: port 1738" in capsys.readouterr().err


def test_start_refuses_mismatched_handoff_dirs(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HANDOFF_DIR", str(tmp_path / "somewhere-else"))
    assert cli.main(["start"]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_start_refuses_while_already_running(tmp_path, capsys):
    _write_state(tmp_path)
    assert cli.main(["start"]) == 1
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


def test_stop_refuses_during_a_posting_run(tmp_path, monkeypatch, capsys):
    _write_state(tmp_path, poster={"state": "running", "pid": os.getpid(), "port": 1})
    monkeypatch.setattr(
        stopguard,
        "read_poster_state",
        lambda port: stopguard.PosterState(reachable=True, active=True),
    )
    killed = []
    monkeypatch.setattr(cli.os, "kill", lambda pid, sig: killed.append(sig))
    assert cli.main(["stop"]) == 1
    assert "refusing to stop" in capsys.readouterr().out
    assert signal.SIGTERM not in killed


def test_stop_force_signals_the_launcher(tmp_path, monkeypatch):
    _write_state(tmp_path, poster={"state": "running", "pid": os.getpid(), "port": 1})
    monkeypatch.setattr(
        stopguard,
        "read_poster_state",
        lambda port: stopguard.PosterState(reachable=True, active=True),
    )
    sent = []

    def fake_kill(pid, sig):
        sent.append(sig)
        if sig == signal.SIGTERM:
            cli.state_path().write_text(json.dumps({"launcher_pid": 0}))

    monkeypatch.setattr(cli.os, "kill", fake_kill)
    monkeypatch.setattr(cli, "_pid_alive", lambda pid: signal.SIGTERM not in sent)
    assert cli.main(["stop", "--force"]) == 0
    assert signal.SIGTERM in sent


def test_stop_decision_skips_poster_when_it_is_not_running(monkeypatch):
    monkeypatch.setattr(
        stopguard,
        "read_poster_state",
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
