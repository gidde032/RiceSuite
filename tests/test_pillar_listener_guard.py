"""Poster's own listener refuses foreign Hosts and cross-origin state changes
(#14, SPEC FR-3), not just the gateway in front of it.

These tests start the real Poster app under uvicorn on a spare loopback port,
exactly as the launcher does, and send it the requests a hostile page could
make. Everything runs against temporary directories: HOME, Poster's data root
and its handoff directory point into a temp dir, Poster runs with
POST_MODE=mock and its scheduler off, and the only state-changing call that
may succeed is the stop hold (an in-memory flag), never /api/post.
"""

import os
import socket
import subprocess
import time

import httpx
import pytest

from ricesuite import SUITE_ROOT
from ricesuite.pillars import BY_NAME, pillar_argv

EVIL = "https://evil.example"
GATEWAY_ORIGINS = ("http://127.0.0.1:8790", "http://localhost:8790")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def poster(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("poster-listener")
    home, data, handoff = tmp / "home", tmp / "data", tmp / "handoff"
    for d in (home, data, handoff):
        d.mkdir()
    port = _free_port()
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(home),
        # This checkout's `ricesuite`, whatever an editable install points at.
        "PYTHONPATH": str(SUITE_ROOT),
        "PYTHONUNBUFFERED": "1",
        "RICEPOSTER_DATA_DIR": str(data),
        "HANDOFF_DIR": str(handoff),
        "POST_MODE": "mock",
        "SCHEDULER_ENABLED": "false",
        "ANTHROPIC_API_KEY": "",
    }
    log = (tmp / "poster.log").open("w")
    proc = subprocess.Popen(
        pillar_argv(BY_NAME["poster"], port),
        cwd=SUITE_ROOT / "poster",
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 60
        while True:
            try:
                if httpx.get(base + "/api/queue", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if proc.poll() is not None or time.monotonic() > deadline:
                log.flush()
                raise AssertionError(
                    "Poster did not come up:\n" + (tmp / "poster.log").read_text()
                )
            time.sleep(0.2)
        yield base, port
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        log.close()


@pytest.mark.parametrize("origin", [EVIL, "null"])
@pytest.mark.parametrize(
    "path", ["/api/stop-hold", "/api/pull-from-clipper", "/api/media/clear"]
)
def test_poster_listener_refuses_a_cross_origin_post(poster, origin, path):
    base, _ = poster
    r = httpx.post(base + path, headers={"origin": origin}, timeout=5)
    assert r.status_code == 403, r.text
    assert r.json() == {"detail": "cross-origin request refused"}


def test_poster_listener_refuses_a_cross_origin_form_post(poster):
    base, _ = poster
    r = httpx.post(
        base + "/api/stop-hold",
        content=b"x=1",
        headers={"origin": EVIL, "content-type": "text/plain"},
        timeout=5,
    )
    assert r.status_code == 403


@pytest.mark.parametrize(
    "host", ["evil.example", "evil.example:{port}", "127.0.0.1:8793x", "[::1]:{port}"]
)
@pytest.mark.parametrize("path", ["/", "/api/queue", "/api/media-stat?name=x"])
def test_poster_listener_refuses_a_foreign_host(poster, host, path):
    base, port = poster
    r = httpx.get(base + path, headers={"host": host.format(port=port)}, timeout=5)
    assert r.status_code == 421, r.text


def test_poster_listener_refuses_a_rebound_state_change(poster):
    base, _ = poster
    r = httpx.post(base + "/api/stop-hold", headers={"host": "evil.example"}, timeout=5)
    assert r.status_code == 421


@pytest.mark.parametrize(
    "headers",
    [
        {},  # the launcher, the stop guard, curl
        {"origin": GATEWAY_ORIGINS[0]},  # a page served through the gateway
        {"origin": GATEWAY_ORIGINS[1]},
        {"origin": "http://127.0.0.1:{port}"},  # Poster run standalone
        {"origin": "http://localhost:{port}", "host": "localhost:{port}"},
    ],
)
def test_poster_listener_accepts_the_suite_and_its_own_pages(poster, headers):
    base, port = poster
    headers = {k: v.format(port=port) for k, v in headers.items()}
    try:
        taken = httpx.post(base + "/api/stop-hold", headers=headers, timeout=5)
        assert taken.status_code == 200, taken.text
        assert taken.json() == {"held": True, "active": False}
    finally:
        released = httpx.delete(base + "/api/stop-hold", headers=headers, timeout=5)
    assert released.status_code == 200 and released.json() == {"held": False}
    assert httpx.get(base + "/", headers=headers, timeout=5).status_code == 200
