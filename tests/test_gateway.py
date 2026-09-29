"""Gateway routing (ADR-001 Q6/Q11, SPEC FR-3, FR-10, FR-11).

Upstream pillars are replaced by an httpx MockTransport, so these tests
exercise the gateway's own routing, header handling and guards.
"""

import json

import httpx
import pytest
from starlette.testclient import TestClient

from ricesuite import gateway

BASE = f"http://127.0.0.1:{gateway.GATEWAY}"


class _Streamed(httpx.AsyncByteStream):
    """A body delivered as a stream, like a real upstream response (an
    in-memory httpx.Response is pre-read and cannot be re-streamed)."""

    def __init__(self, data: bytes):
        self.data = data

    async def __aiter__(self):
        yield self.data


def streamed(response: httpx.Response) -> httpx.Response:
    return httpx.Response(
        response.status_code,
        headers=response.headers,
        stream=_Streamed(response.content),
    )


def mock_client(handler) -> httpx.AsyncClient:
    def wrapped(request):
        return streamed(handler(request))

    return httpx.AsyncClient(transport=httpx.MockTransport(wrapped))


@pytest.fixture
def upstream(monkeypatch):
    seen: list[httpx.Request] = []
    responses: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        request.read()
        key = (request.url.port, request.url.path)
        if key in responses:
            return responses[key](request)
        return httpx.Response(
            200,
            json={"port": request.url.port, "path": request.url.path},
            headers={"x-upstream": "yes", "connection": "close"},
        )

    monkeypatch.setattr(gateway, "_client", mock_client(handler))
    return seen, responses


@pytest.fixture
def client():
    return TestClient(gateway.app, base_url=BASE)


@pytest.mark.parametrize(
    ("prefix", "pillar"),
    [("/search", "searcher"), ("/clip", "clipper"), ("/post", "poster")],
)
def test_prefix_routes_to_its_own_pillar_with_prefix_stripped(
    upstream, client, prefix, pillar
):
    seen, _ = upstream
    r = client.get(f"{prefix}/api/thing?a=1&b=two")
    assert r.status_code == 200
    assert r.json() == {"port": gateway.PORTS[pillar], "path": "/api/thing"}
    assert seen[-1].url.host == "127.0.0.1"
    assert seen[-1].url.query == b"a=1&b=two"
    assert r.headers["x-upstream"] == "yes"
    assert "connection" not in r.headers  # hop-by-hop, not forwarded


def test_bare_prefix_redirects_to_trailing_slash(upstream, client):
    """Relative URLs in pillar pages only resolve under the prefix when the
    page URL ends in a slash."""
    r = client.get("/post", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/post/"


def test_pillar_root_page_is_proxied(upstream, client):
    seen, _ = upstream
    client.get("/clip/")
    assert seen[-1].url.path == "/"


def test_request_body_and_method_pass_through(upstream, client):
    seen, _ = upstream
    r = client.post("/post/api/generate-caption", json={"style": "generic"})
    assert r.status_code == 200
    assert seen[-1].method == "POST"
    assert json.loads(seen[-1].content) == {"style": "generic"}
    # The browser's Host is not forwarded; the pillar sees its own address.
    assert seen[-1].headers["host"] == f"127.0.0.1:{gateway.PORTS['poster']}"


def test_upstream_status_and_body_are_preserved(upstream, client):
    _, responses = upstream
    responses[(gateway.PORTS["clipper"], "/api/jobs/x")] = lambda req: httpx.Response(
        404, json={"detail": "no such job"}
    )
    r = client.get("/clip/api/jobs/x")
    assert r.status_code == 404 and r.json() == {"detail": "no such job"}


def test_root_absolute_redirects_stay_under_the_prefix(upstream, client):
    _, responses = upstream
    responses[(gateway.PORTS["searcher"], "/old")] = lambda req: httpx.Response(
        302, headers={"location": "/new"}
    )
    r = client.get("/search/old", follow_redirects=False)
    assert r.headers["location"] == "/search/new"


def test_down_pillar_is_a_502_naming_the_tab(monkeypatch, client):
    def refuse(request):
        raise httpx.ConnectError("refused", request=request)

    monkeypatch.setattr(gateway, "_client", mock_client(refuse))
    r = client.get("/clip/api/health")
    assert r.status_code == 502
    assert "Clip" in r.json()["detail"]


def test_shell_is_served_at_the_root(client):
    r = client.get("/")
    assert r.status_code == 200
    for label in ("Search", "Clip", "Post", 'data-src="search/"', "shell/shell.js"):
        assert label in r.text
    assert client.get("/shell/shell.css").status_code == 200
    slate = client.get("/shell/slate.css")
    assert slate.status_code == 200
    assert slate.headers["content-type"].startswith("text/css")
    assert "--backdrop: #04060A" in slate.text


def test_foreign_host_header_is_refused(upstream, client):
    """DNS-rebinding guard: Poster's API is unauthenticated."""
    r = client.get("/post/api/queue", headers={"host": "evil.example:8790"})
    assert r.status_code == 421
    assert not upstream[0]


def test_localhost_host_header_is_accepted(upstream, client):
    r = client.get("/post/api/queue", headers={"host": f"localhost:{gateway.GATEWAY}"})
    assert r.status_code == 200


def test_cross_origin_state_change_is_refused(upstream, client):
    r = client.post(
        "/post/api/post", json={}, headers={"origin": "https://evil.example"}
    )
    assert r.status_code == 403
    assert not upstream[0]


def test_same_origin_state_change_is_allowed(upstream, client):
    r = client.post("/post/api/queue", json={}, headers={"origin": BASE})
    assert r.status_code == 200


def test_cross_origin_reads_are_left_to_the_browser(upstream, client):
    r = client.get("/post/api/queue", headers={"origin": "https://evil.example"})
    assert r.status_code == 200


def test_home_reports_what_each_consumer_would_pull_next(monkeypatch, client):
    """The home view asks the consumers themselves, so it can never disagree
    with the next Pull (Clipper's Searcher inbox, Poster's Clipper inbox)."""

    def handler(request):
        port, path = request.url.port, request.url.path
        if port == gateway.PORTS["searcher"] and path == "/api/profiles":
            return httpx.Response(
                200, json=[{"candidates": 4, "selected": 1}, {"candidates": 1}]
            )
        if port == gateway.PORTS["clipper"] and path == "/api/searcher-inbox":
            return httpx.Response(
                200, json={"batches": [{"batch_id": "s1", "clip_count": 2}]}
            )
        if port == gateway.PORTS["poster"] and path == "/api/handoff/inbox":
            return httpx.Response(
                200,
                json={
                    "batches": [{"batch_id": "c2", "clip_count": 3}],
                    "unacknowledged": "c1",
                    "error": None,
                },
            )
        if port == gateway.PORTS["poster"] and path == "/api/queue":
            return httpx.Response(200, json={"batches": []})
        return httpx.Response(404)

    monkeypatch.setattr(gateway, "_client", mock_client(handler))
    data = client.get("/api/suite/home").json()
    assert data["search"] == {"candidates": 5, "selected": 1}
    assert data["to_clipper"] == [{"batch_id": "s1", "clip_count": 2}]
    assert [b["batch_id"] for b in data["to_poster"]] == ["c1", "c2"]
    assert data["scheduled"] == []


def test_home_tolerates_down_pillars(monkeypatch, client):
    def handler(request):
        if request.url.port == gateway.PORTS["searcher"]:
            return httpx.Response(200, json=[])
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(gateway, "_client", mock_client(handler))
    data = client.get("/api/suite/home").json()
    assert data["search"] == {"candidates": 0, "selected": 0}
    assert data["to_clipper"] is None
    assert data["to_poster"] is None
    assert data["scheduled"] is None


def test_home_reports_scheduled_batches(monkeypatch, client):
    def handler(request):
        if request.url.path == "/api/queue":
            return httpx.Response(
                200,
                json={
                    "batches": [
                        {
                            "id": "q1",
                            "fire_time": "t",
                            "status": "pending",
                            "slots": [{"caption": "private"}],
                        }
                    ]
                },
            )
        return httpx.Response(200, json=[])

    monkeypatch.setattr(gateway, "_client", mock_client(handler))
    data = client.get("/api/suite/home").json()
    assert data["scheduled"] == [{"id": "q1", "fire_time": "t", "status": "pending"}]


def test_status_lists_the_three_tabs(client, tmp_path, monkeypatch):
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"children": {"poster": {"state": "running"}}}))
    monkeypatch.setattr(gateway, "STATE_FILE", str(state))
    data = client.get("/api/suite/status").json()
    assert [p["tab"] for p in data["pillars"]] == ["Search", "Clip", "Post"]
    assert data["state"]["children"]["poster"]["state"] == "running"
