"""The shared Host/Origin guard every listener in the suite runs (#14,
SPEC FR-3), driven over a small app. tests/test_pillar_listener_guard.py
checks it on Poster's real listener."""

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from ricesuite import localguard

EVIL = "https://evil.example"
GATEWAY_ORIGINS = ("http://127.0.0.1:8790", "http://localhost:8790")


async def _ok(request):
    return PlainTextResponse(request.method)


async def _ws(websocket):
    await websocket.accept()
    await websocket.send_text("hello")
    await websocket.close()


def _guarded(**kwargs):
    inner = Starlette(
        routes=[
            Route("/", _ok, methods=["GET", "HEAD", "OPTIONS", "POST", "DELETE"]),
            WebSocketRoute("/ws", _ws),
        ]
    )
    return localguard.LocalGuard(inner, **kwargs)


@pytest.fixture
def listener():
    """A guarded app answering as a listener bound to 127.0.0.1:8793."""
    return TestClient(
        _guarded(trusted_origins=GATEWAY_ORIGINS), base_url="http://127.0.0.1:8793"
    )


@pytest.mark.parametrize("host", ["127.0.0.1:8793", "localhost:8793"])
def test_loopback_host_for_the_bound_port_is_answered(listener, host):
    assert listener.get("/", headers={"host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8793",
        "127.0.0.1",  # no port
        "127.0.0.1:8790",  # another listener's port
        "localhost:1738",
        "[::1]:8793",  # not bound: every listener binds 127.0.0.1 only
        "0.0.0.0:8793",
        "",
    ],
)
def test_foreign_host_is_refused_with_421(listener, host):
    r = listener.get("/", headers={"host": host})
    assert r.status_code == 421
    assert r.json() == {"detail": "unexpected Host"}


def test_port_comes_from_the_bound_socket_so_a_standalone_port_is_guarded():
    standalone = TestClient(_guarded(), base_url="http://127.0.0.1:1738")
    assert standalone.get("/").status_code == 200
    assert standalone.get("/", headers={"host": "127.0.0.1:8793"}).status_code == 421


def test_a_fixed_port_overrides_the_bound_socket():
    """The gateway's mode: it answers only for its configured port."""
    app = _guarded(port=8790)
    assert TestClient(app, base_url="http://127.0.0.1:8790").get("/").status_code == 200
    assert TestClient(app, base_url="http://127.0.0.1:9999").get("/").status_code == 421


async def _call(app, scope):
    sent = []

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        sent.append(message)

    await app(scope, receive, send)
    return sent


@pytest.mark.anyio
async def test_a_listener_with_no_tcp_port_refuses_everything():
    """A Unix-socket listener has no port to check the Host against, so the
    guard fails closed rather than guessing."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(b"host", b"127.0.0.1:8793")],
        "server": ("/tmp/pillar.sock", None),
    }
    sent = await _call(_guarded(), scope)
    assert sent[0]["status"] == 421
    sent = await _call(_guarded(), {**scope, "server": None})
    assert sent[0]["status"] == 421


@pytest.mark.parametrize(
    "origin",
    [
        "http://127.0.0.1:8793",
        "http://localhost:8793",
        *GATEWAY_ORIGINS,
    ],
)
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_state_change_from_own_or_gateway_origin_is_allowed(listener, origin, method):
    r = listener.request(method, "/", headers={"origin": origin})
    assert r.status_code == 200 and r.text == method


def test_state_change_without_origin_is_allowed(listener):
    """The launcher, the stop guard, `rice status` and curl send no Origin."""
    assert listener.post("/").status_code == 200


@pytest.mark.parametrize(
    "origin",
    [
        EVIL,
        "null",  # sandboxed iframes, file:// pages, some redirects
        "http://127.0.0.1:8792",  # a sibling pillar's own page
        "http://localhost:1738",
        "https://127.0.0.1:8793",  # scheme matters
        "http://127.0.0.1:8793/",  # an Origin is never followed by a path
        "http://[::1]:8793",
        "",
    ],
)
@pytest.mark.parametrize("method", ["POST", "DELETE"])
def test_cross_origin_state_change_is_refused_with_403(listener, origin, method):
    r = listener.request(method, "/", headers={"origin": origin})
    assert r.status_code == 403
    assert r.json() == {"detail": "cross-origin request refused"}


def test_a_simple_form_post_from_another_site_is_refused(listener):
    """A form or `text/plain` fetch needs no CORS preflight, so the guard,
    not the browser, is what stops it."""
    for content_type in ("application/x-www-form-urlencoded", "text/plain"):
        r = listener.post(
            "/",
            content=b"slots=1",
            headers={"origin": EVIL, "content-type": content_type},
        )
        assert r.status_code == 403


def test_a_repeated_origin_header_must_pass_every_time(listener):
    r = listener.post(
        "/", headers=[("origin", "http://127.0.0.1:8793"), ("origin", EVIL)]
    )
    assert r.status_code == 403


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
def test_cross_origin_reads_are_left_to_the_browser(listener, method):
    assert listener.request(method, "/", headers={"origin": EVIL}).status_code == 200


def test_the_default_trusted_origin_is_the_suite_gateway(monkeypatch):
    monkeypatch.delenv("RICESUITE_GATEWAY_PORT", raising=False)
    assert localguard.gateway_origins() == {
        "http://127.0.0.1:8790",
        "http://localhost:8790",
    }


def test_the_launcher_can_name_the_gateway_port(monkeypatch):
    monkeypatch.setenv("RICESUITE_GATEWAY_PORT", "9123")
    assert localguard.gateway_origins() == {
        "http://127.0.0.1:9123",
        "http://localhost:9123",
    }


@pytest.mark.parametrize("bad", ["", "http://evil.example", "80a", "0", "70000"])
def test_a_malformed_gateway_port_fails_loudly(monkeypatch, bad):
    monkeypatch.setenv("RICESUITE_GATEWAY_PORT", bad)
    with pytest.raises(ValueError, match="RICESUITE_GATEWAY_PORT"):
        localguard.gateway_origins()


def test_websocket_from_own_origin_is_accepted(listener):
    with listener.websocket_connect(
        "ws://127.0.0.1:8793/ws", headers={"origin": "http://127.0.0.1:8793"}
    ) as ws:
        assert ws.receive_text() == "hello"


@pytest.mark.parametrize(
    ("url", "headers"),
    [
        ("ws://127.0.0.1:8793/ws", {"origin": EVIL}),
        ("ws://127.0.0.1:8793/ws", {"origin": "null"}),
        ("ws://evil.example:8793/ws", {}),
    ],
)
def test_websocket_from_another_origin_or_host_is_refused(listener, url, headers):
    """Browsers apply no CORS to WebSockets, so the handshake's Origin is
    checked whatever the method."""
    with (
        pytest.raises(WebSocketDisconnect) as refused,
        listener.websocket_connect(url, headers=headers),
    ):
        pass
    assert refused.value.code == 1008


@pytest.mark.anyio
async def test_lifespan_passes_through():
    seen = []

    async def inner(scope, receive, send):
        seen.append(scope["type"])

    await localguard.LocalGuard(inner)({"type": "lifespan"}, None, None)
    assert seen == ["lifespan"]
