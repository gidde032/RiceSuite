"""The API refuses foreign Hosts and cross-origin state changes (suite #14,
SPEC FR-3).

Poster's API is unauthenticated and can post to real accounts, and a page in
the maintainer's browser can reach Poster's loopback port directly, bypassing
the RiceSuite gateway. So Poster enforces the same guard itself, for the port
it is bound to: 8793 behind the suite, 1738 when run standalone. The suite's
tests/test_pillar_listener_guard.py checks the same on a real listener.

Every admitted request here is the stop hold (an in-memory flag, released at
the end); nothing reaches /api/post.
"""

import pytest
from fastapi.testclient import TestClient

from backend import main, run_guard

EVIL = "https://evil.example"


@pytest.fixture(params=[1738, 8793], ids=["standalone", "suite"])
def guarded(request):
    client = TestClient(main.app, base_url=f"http://127.0.0.1:{request.param}")
    yield client
    run_guard.release_hold()


@pytest.mark.parametrize("origin", [EVIL, "null", "http://127.0.0.1:8792"])
@pytest.mark.parametrize(
    "path", ["/api/stop-hold", "/api/pull-from-clipper", "/api/media/clear"]
)
def test_cross_origin_state_change_is_refused(guarded, origin, path):
    r = guarded.post(path, headers={"origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "cross-origin request refused"}
    assert not run_guard.is_held()


@pytest.mark.parametrize("host", ["evil.example", "evil.example:1738", "[::1]:8793"])
@pytest.mark.parametrize("path", ["/", "/api/queue", "/api/media-stat?name=x"])
def test_foreign_host_is_refused(guarded, host, path):
    assert guarded.get(path, headers={"host": host}).status_code == 421


def test_rebound_state_change_is_refused(guarded):
    r = guarded.post("/api/stop-hold", headers={"host": "evil.example"})
    assert r.status_code == 421
    assert not run_guard.is_held()


@pytest.mark.parametrize(
    "origin", [None, "own", "http://127.0.0.1:8790", "http://localhost:8790"]
)
def test_suite_and_own_pages_are_admitted(guarded, origin):
    if origin == "own":
        origin = str(guarded.base_url).rstrip("/")
    headers = {"origin": origin} if origin else {}
    taken = guarded.post("/api/stop-hold", headers=headers)
    assert taken.status_code == 200 and taken.json()["held"] is True
    released = guarded.delete("/api/stop-hold", headers=headers)
    assert released.status_code == 200 and not run_guard.is_held()
