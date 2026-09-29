"""End to end: `rice` starts the real gateway and the three real pillars.

Everything runs against temporary directories: HOME, every data root and
both handoff directories point into tmp_path, Poster runs with
POST_MODE=mock and its scheduler off, and all ports are spare ones (the
module constants are overridden in the launcher's own process). No network
beyond loopback, no live data.
"""

import json
import os
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from ricesuite import SUITE_ROOT

WRAPPER = """
import json, sys
from ricesuite import ports
cfg = json.loads(sys.argv[1])
ports.LEGACY_PORTS = {int(k): v for k, v in cfg["legacy"].items()}
ports.GATEWAY_PORT = cfg["gateway"]
ports.PILLAR_PORTS.update(cfg["pillars"])
from ricesuite import cli
sys.exit(cli.main(sys.argv[2:]))
"""


def _free_ports(n):
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    found = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return found


@pytest.fixture
def suite(tmp_path):
    gateway, searcher, clipper, poster, legacy = _free_ports(5)
    home = tmp_path / "home"
    poster_data = tmp_path / "poster-data"
    for d in (home, poster_data):
        d.mkdir()
    env_file = tmp_path / "ricesuite.env"
    env_file.write_text(
        "\n".join(
            [
                f"RICESEARCHER_DATA_DIR={tmp_path / 'searcher-data'}",
                f"RICESEARCHER_HANDOFF_DIR={tmp_path / 'searcher-handoff'}",
                f"RICECLIPPER_HANDOFF_DIR={tmp_path / 'clipper-handoff'}",
                f"RICEPOSTER_DATA_DIR={poster_data}",
                "POST_MODE=mock",
                "SCHEDULER_ENABLED=false",
                "ANTHROPIC_API_KEY=",
                "",
            ]
        )
    )
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        "RICESUITE_ENV": str(env_file),
        "RICESUITE_RUN_DIR": str(tmp_path / "run"),
    }
    config = json.dumps(
        {
            "legacy": {str(legacy): "RicePoster"},
            "gateway": gateway,
            "pillars": {"searcher": searcher, "clipper": clipper, "poster": poster},
        }
    )

    def rice(*args, **kwargs):
        return subprocess.run(
            [sys.executable, "-c", WRAPPER, config, *args],
            env=env,
            cwd=SUITE_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
            **kwargs,
        )

    def start():
        return subprocess.Popen(
            [sys.executable, "-c", WRAPPER, config, "start"],
            env=env,
            cwd=SUITE_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )

    class Suite:
        pass

    s = Suite()
    s.tmp, s.env, s.rice, s.start = tmp_path, env, rice, start
    s.gateway = f"http://127.0.0.1:{gateway}"
    s.gateway_port = gateway
    s.pillar_ports = {"searcher": searcher, "clipper": clipper, "poster": poster}
    s.legacy_port = legacy
    s.state_file = tmp_path / "run" / "state.json"
    s.poster_data = poster_data
    s.home = home
    return s


def _state(s):
    try:
        return json.loads(s.state_file.read_text())
    except (OSError, ValueError):
        return None


def _wait(predicate, timeout=60.0, interval=0.25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("timed out waiting")


def _all_up(s):
    state = _state(s)
    if not state or any(c["state"] != "running" for c in state["children"].values()):
        return False
    try:
        for path in (
            "/",
            "/search/api/profiles",
            "/clip/api/health",
            "/post/api/queue",
        ):
            if httpx.get(s.gateway + path, timeout=2).status_code != 200:
                return False
    except httpx.HTTPError:
        return False
    return state


def test_rice_runs_the_whole_suite_behind_one_port(suite):
    proc = suite.start()
    try:
        state = _wait(lambda: _all_up(suite), timeout=90)
        assert set(state["children"]) == {"searcher", "clipper", "poster", "gateway"}

        # One front door: shell plus each pillar's own page and API.
        assert "Search" in httpx.get(suite.gateway + "/").text
        assert httpx.get(suite.gateway + "/search/").status_code == 200
        assert isinstance(
            httpx.get(suite.gateway + "/search/api/profiles").json(), list
        )
        assert httpx.get(suite.gateway + "/clip/").status_code == 200
        assert httpx.get(suite.gateway + "/post/").status_code == 200
        assert httpx.get(suite.gateway + "/post/api/queue").json() == {"batches": []}
        # Each document loads one canonical Slate file through its own prefix.
        canonical = (SUITE_ROOT / "ricesuite/shell/slate.css").read_bytes()
        for path in (
            "/shell/slate.css",
            "/search/static/slate.css",
            "/clip/slate.css",
            "/post/static/slate.css",
        ):
            asset = httpx.get(suite.gateway + path)
            assert asset.status_code == 200, path
            assert asset.headers["content-type"].startswith("text/css"), path
            assert asset.content == canonical, path
        home = httpx.get(suite.gateway + "/api/suite/home").json()
        assert home["to_clipper"] == [] and home["to_poster"] == []
        assert home["scheduled"] == []

        # Poster's data landed in the temp data root, never in the checkout.
        assert (suite.poster_data / "media").is_dir()

        # Every listener, not just the gateway, refuses a hostile page (#14).
        for name, port in suite.pillar_ports.items():
            direct = f"http://127.0.0.1:{port}"
            refused = httpx.post(
                direct + "/api/stop-hold", headers={"origin": "https://evil.example"}
            )
            assert refused.status_code == 403, name
            rebound = httpx.get(direct + "/", headers={"host": "evil.example"})
            assert rebound.status_code == 421, name
        # No other site may frame the shell or a pillar page (#38); the shell
        # frames each pillar page from the gateway's own origin.
        for path in ("/", "/search/", "/clip/", "/post/"):
            framed = httpx.get(suite.gateway + path)
            assert framed.headers["x-frame-options"] == "SAMEORIGIN", path
            assert framed.headers.get_list("content-security-policy") == [
                "frame-ancestors 'self'"
            ], path
        # A page served through the gateway still changes state: Poster admits
        # the gateway's origin on the port the launcher actually used.
        for origin_host in ("127.0.0.1", "localhost"):
            origin = {"origin": f"http://{origin_host}:{suite.gateway_port}"}
            held = httpx.post(suite.gateway + "/post/api/stop-hold", headers=origin)
            assert held.status_code == 200 and held.json()["held"] is True, held.text
            released = httpx.delete(
                suite.gateway + "/post/api/stop-hold", headers=origin
            )
            assert released.status_code == 200, released.text

        status = suite.rice("status")
        assert status.returncode == 0 and "poster" in status.stdout

        # A crashed pillar is restarted; nothing else is.
        clipper_pid = state["children"]["clipper"]["pid"]
        os.killpg(clipper_pid, signal.SIGKILL)
        restarted = _wait(
            lambda: (
                (st := _state(suite))
                and st["children"]["clipper"]["restarts"] == 1
                and st["children"]["clipper"]["state"] == "running"
                and st
            ),
            timeout=60,
        )
        assert restarted["children"]["clipper"]["pid"] != clipper_pid
        assert all(
            restarted["children"][n]["restarts"] == 0
            for n in ("searcher", "poster", "gateway")
        )
        _wait(lambda: _all_up(suite), timeout=60)

        # A second launcher refuses: the suite is already running.
        again = suite.rice("start")
        assert again.returncode == 1

        stop = suite.rice("stop")
        assert stop.returncode == 0, stop.stdout + stop.stderr
        proc.wait(timeout=30)
        assert proc.returncode == 0
        assert not suite.state_file.exists()
        with pytest.raises(httpx.HTTPError):
            httpx.get(suite.gateway + "/", timeout=1)
    finally:
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=30)


def test_rice_refuses_to_start_while_an_old_app_port_answers(suite):
    listener = socket.socket()
    listener.bind(("127.0.0.1", suite.legacy_port))
    listener.listen()
    try:
        result = suite.rice("start")
    finally:
        listener.close()
    assert result.returncode == 1
    assert str(suite.legacy_port) in result.stderr
    assert "RicePoster" in result.stderr
    assert not suite.state_file.exists()
