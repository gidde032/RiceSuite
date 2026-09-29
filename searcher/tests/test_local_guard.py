"""The review app refuses foreign Hosts and cross-origin state changes
(RiceSuite #14, suite SPEC FR-3).

A page in the maintainer's browser can reach this app's loopback port
directly, bypassing the RiceSuite gateway, so the app enforces the same guard
itself. The port it answers for is the one it is bound to: 8791 behind the
suite, 8765 when run standalone.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.config import Config
from ricesearcher.web.app import create_app

EVIL = "https://evil.example"
# A state change that is harmless when admitted: there is no such slice.
SELECT = "/api/slices/no-such-slice/status"


@pytest.fixture(params=[8765, 8791], ids=["standalone", "suite"])
def client(request: pytest.FixtureRequest, tmp_path: Path) -> TestClient:
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    return TestClient(create_app(cfg), base_url=f"http://127.0.0.1:{request.param}")


@pytest.mark.parametrize("origin", [EVIL, "null", "http://127.0.0.1:8792"])
def test_cross_origin_state_change_is_refused(client: TestClient, origin: str) -> None:
    r = client.post(SELECT, json={"status": "selected"}, headers={"origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "cross-origin request refused"}


def test_cross_origin_form_post_is_refused(client: TestClient) -> None:
    r = client.post(
        "/api/cache/clear",
        content=b"x=1",
        headers={"origin": EVIL, "content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 403


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8765", "[::1]:8765"])
@pytest.mark.parametrize("path", ["/", "/api/profiles", SELECT])
def test_foreign_host_is_refused(client: TestClient, host: str, path: str) -> None:
    method = "POST" if path == SELECT else "GET"
    r = client.request(method, path, headers={"host": host})
    assert r.status_code == 421


def test_own_origin_gateway_origin_and_no_origin_are_admitted(
    client: TestClient,
) -> None:
    own = str(client.base_url).rstrip("/")
    for headers in (
        {},
        {"origin": own},
        {"origin": "http://127.0.0.1:8790"},
        {"origin": "http://localhost:8790"},
    ):
        r = client.post(SELECT, json={"status": "selected"}, headers=headers)
        assert r.status_code == 404, (headers, r.text)  # reached the app


def test_cross_origin_reads_are_left_to_the_browser(client: TestClient) -> None:
    assert client.get("/api/profiles", headers={"origin": EVIL}).status_code == 200


FRAMING = "frame-ancestors 'self'"


def test_pages_refuse_to_be_framed_by_another_site(client: TestClient) -> None:
    for path in ("/", "/media", "/api/profiles"):
        r = client.get(path)
        assert r.headers["x-frame-options"] == "SAMEORIGIN", path
        assert r.headers["content-security-policy"] == FRAMING, path


def test_cached_media_is_served_sandboxed(tmp_path: Path) -> None:
    """Cached source media came from outside; opened directly, it must not
    run script as a suite origin (RiceSuite #38)."""
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    client = TestClient(create_app(cfg), base_url="http://127.0.0.1:8765")
    (cfg.cache_dir / "ab").mkdir(parents=True, exist_ok=True)
    (cfg.cache_dir / "ab" / "x.svg").write_text("<svg><script>1</script></svg>")
    r = client.get("/cache/ab/x.svg")
    assert r.status_code == 200
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == (
        f"default-src 'none'; sandbox; {FRAMING}"
    )
