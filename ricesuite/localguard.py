"""The Host/Origin guard every listener in the suite runs (SPEC FR-3, #14).

Poster's API is unauthenticated and can post to real accounts, and each pillar
listens on its own loopback TCP port, which a page in the maintainer's browser
can reach without going through the gateway. So the gateway and all three
pillar apps wrap themselves in :class:`LocalGuard`, which refuses:

* a request not addressed to ``127.0.0.1:<port>`` or ``localhost:<port>``,
  with 421 (a DNS-rebinding page reaches the port under a foreign Host);
* a state-changing request, or a WebSocket handshake, whose ``Origin`` is not
  the listener's own loopback origin or a trusted one, with 403 (a page on
  another site can send a form POST or a no-preflight fetch).

A refused WebSocket handshake, for either reason, is closed before it is
accepted, which the server answers with 403.

Every HTTP response, refusals included, also says that only the listener's own
origin may frame it (``X-Frame-Options: SAMEORIGIN`` and CSP
``frame-ancestors 'self'``), so a page on another site cannot frame a pillar
and trick the maintainer into clicking it (#38); the gateway shell frames each
pillar page from its own origin. Responses under ``sandboxed_paths`` (files a
user or a download supplied) are served ``nosniff`` with a policy that runs no
script, so such a file opened directly cannot act as a suite origin.

``<port>`` is the port the listener is bound to, so a pillar run standalone on
its old port is guarded too. A request with no Origin is allowed: the
launcher, the stop guard, ``rice status`` and curl send none, and browsers
send one with every cross-origin state-changing request.
"""

from __future__ import annotations

import os
from collections.abc import Iterable

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketClose

from ricesuite.ports import GATEWAY_PORT

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
FRAMING_POLICY = "frame-ancestors 'self'"
SANDBOX_POLICY = f"default-src 'none'; sandbox; {FRAMING_POLICY}"


def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def loopback_origins(port: int) -> set[str]:
    return {f"http://{host}" for host in allowed_hosts(port)}


def gateway_origins() -> set[str]:
    """The gateway's origins, which every pillar trusts: pages served through
    the gateway send them. The launcher names the gateway port in
    ``RICESUITE_GATEWAY_PORT``; a pillar run on its own assumes SPEC §2.1's."""
    raw = os.environ.get("RICESUITE_GATEWAY_PORT")
    if raw is None:
        return loopback_origins(GATEWAY_PORT)
    if not (raw.isascii() and raw.isdigit() and 0 < int(raw) < 65536):
        raise ValueError(f"RICESUITE_GATEWAY_PORT={raw!r} is not a port number")
    return loopback_origins(int(raw))


def _bound_port(scope: Scope) -> int | None:
    server = scope.get("server")
    return server[1] if server else None


def _harden(message: Message, sandboxed: bool) -> None:
    headers = MutableHeaders(scope=message)
    if sandboxed:
        headers["x-content-type-options"] = "nosniff"
        headers["content-security-policy"] = SANDBOX_POLICY
    elif not any(
        "frame-ancestors" in policy
        for policy in headers.getlist("content-security-policy")
    ):
        # Appended, so a policy the app set itself still applies.
        headers.append("content-security-policy", FRAMING_POLICY)
    if "x-frame-options" not in headers:
        headers["x-frame-options"] = "SAMEORIGIN"


class LocalGuard:
    """ASGI middleware refusing foreign Hosts and cross-origin state changes.

    ``port`` fixes the port requests must be addressed to (the gateway's
    configured port); by default it is the listener's bound port, and a
    listener without one (a Unix socket) refuses everything.
    ``trusted_origins`` are admitted besides the listener's own.
    ``sandboxed_paths`` are path prefixes that serve user-supplied files.
    """

    def __init__(
        self,
        app: ASGIApp,
        port: int | None = None,
        trusted_origins: Iterable[str] = (),
        sandboxed_paths: Iterable[str] = (),
    ) -> None:
        self.app = app
        self.port = port
        self.trusted_origins = frozenset(trusted_origins)
        self.sandboxed_paths = tuple(sandboxed_paths)

    def _refusal(self, scope: Scope) -> tuple[int, str] | None:
        port = self.port if self.port is not None else _bound_port(scope)
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        if port is None or len(hosts) != 1 or hosts[0] not in allowed_hosts(port):
            return 421, "unexpected Host"
        if scope["type"] == "http" and scope["method"] in SAFE_METHODS:
            return None
        allowed = loopback_origins(port) | self.trusted_origins
        if any(origin not in allowed for origin in headers.getlist("origin")):
            return 403, "cross-origin request refused"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            sandboxed = scope["path"].startswith(self.sandboxed_paths)
            inner_send = send

            async def send(message: Message) -> None:
                if message["type"] == "http.response.start":
                    _harden(message, sandboxed)
                await inner_send(message)

        if scope["type"] in ("http", "websocket"):
            refusal = self._refusal(scope)
            if refusal is not None:
                status, detail = refusal
                if scope["type"] == "websocket":
                    await WebSocketClose(code=1008, reason=detail)(scope, receive, send)
                else:
                    await JSONResponse({"detail": detail}, status)(scope, receive, send)
                return
        await self.app(scope, receive, send)
