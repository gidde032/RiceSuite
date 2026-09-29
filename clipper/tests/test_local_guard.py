"""The review app refuses foreign Hosts and cross-origin state changes (suite
#14, SPEC FR-3).

A page in the maintainer's browser can reach this app's loopback port
directly, bypassing the RiceSuite gateway, so the app enforces the same guard
itself. The port it answers for is the one it is bound to: 8792 behind the
suite, 8000 when run standalone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app import jobs, main

EVIL = "https://evil.example"
# A state change that is harmless when admitted: there is no such job.
RENDER = "/api/jobs/no-such-job/render"


@pytest.fixture(params=[8000, 8792], ids=["standalone", "suite"])
def client(request, tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    return TestClient(main.app, base_url=f"http://127.0.0.1:{request.param}")


@pytest.mark.parametrize("origin", [EVIL, "null", "http://127.0.0.1:8791"])
def test_cross_origin_state_change_is_refused(client, origin):
    r = client.post(RENDER, json={"words": []}, headers={"origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "cross-origin request refused"}


def test_cross_origin_form_post_is_refused(client):
    r = client.post(
        "/api/media/clear",
        content=b"x=1",
        headers={"origin": EVIL, "content-type": "text/plain"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8000", "[::1]:8000"])
@pytest.mark.parametrize("path", ["/", "/api/media-info", RENDER])
def test_foreign_host_is_refused(client, host, path):
    method = "POST" if path == RENDER else "GET"
    assert client.request(method, path, headers={"host": host}).status_code == 421


def test_own_origin_gateway_origin_and_no_origin_are_admitted(client):
    own = str(client.base_url).rstrip("/")
    for headers in (
        {},
        {"origin": own},
        {"origin": "http://127.0.0.1:8790"},
        {"origin": "http://localhost:8790"},
    ):
        r = client.post(RENDER, json={"words": []}, headers=headers)
        assert r.status_code == 404, (headers, r.text)  # reached the app


def test_cross_origin_reads_are_left_to_the_browser(client):
    assert client.get("/api/media-info", headers={"origin": EVIL}).status_code == 200
