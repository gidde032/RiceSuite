"""The front door: one localhost port for the whole suite (ADR-001 Q6, Q11).

* ``/`` serves the Slate shell (top bar with Search / Clip / Post tabs and a
  home view).
* ``/search/…``, ``/clip/…``, ``/post/…`` are proxied to that pillar with the
  prefix stripped. Each pillar page uses relative URLs, so its API calls stay
  under its own prefix and reach its own pillar.
* ``/api/suite/…`` is the gateway's own read-only API for the shell.

The gateway adds no capability a pillar did not already expose on localhost.
It does refuse requests whose Host is not this loopback address (a DNS
rebinding guard) and state-changing requests from another origin, because
Poster's API is unauthenticated and can post to real accounts.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from ricesuite import env as suite_env
from ricesuite import home
from ricesuite.pillars import PILLARS
from ricesuite.ports import GATEWAY_PORT, HOST, PILLAR_PORTS

SHELL_DIR = Path(__file__).resolve().parent / "shell"

# Set by the launcher for the gateway process: the ports actually in use and
# the state file. Defaults match SPEC §2.1 so the module imports standalone.
_CONFIG = json.loads(os.environ.get("RICESUITE_GATEWAY_CONFIG") or "{}")
GATEWAY = int(_CONFIG.get("gateway_port", GATEWAY_PORT))
PORTS: dict[str, int] = {**PILLAR_PORTS, **_CONFIG.get("pillar_ports", {})}
STATE_FILE = _CONFIG.get("state_file")

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

# No read timeout: Poster's Post All request stays open for the whole run.
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=5.0, read=None, write=None, pool=None),
    follow_redirects=False,
)


def allowed_hosts(port: int = GATEWAY) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


def _origin_ok(request: Request) -> bool:
    origin = request.headers.get("origin")
    if origin is None or request.method in SAFE_METHODS:
        return True
    return origin in {f"http://{h}" for h in allowed_hosts()}


def upstream_url(port: int, path: str, query: str) -> str:
    return f"http://{HOST}:{port}/{path}" + (f"?{query}" if query else "")


async def proxy(request: Request, pillar) -> Response:
    port = PORTS[pillar.name]
    url = upstream_url(port, request.path_params.get("path", ""), request.url.query)
    headers = [
        (k, v) for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP
    ]
    has_body = "content-length" in request.headers or (
        "transfer-encoding" in request.headers
    )
    upstream_request = _client.build_request(
        request.method,
        url,
        headers=headers,
        content=request.stream() if has_body else None,
    )
    try:
        upstream = await _client.send(upstream_request, stream=True)
    except httpx.TransportError:
        return JSONResponse(
            {"detail": f"{pillar.tab} is not answering (it may be restarting)."},
            status_code=502,
        )
    response_headers = {
        k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP
    }
    location = response_headers.get("location")
    if location and location.startswith("/") and not location.startswith("//"):
        response_headers["location"] = pillar.prefix + location
    return StreamingResponse(
        upstream.aiter_raw(),
        status_code=upstream.status_code,
        headers=response_headers,
        background=BackgroundTask(upstream.aclose),
    )


def _proxy_route(pillar):
    async def endpoint(request: Request) -> Response:
        return await proxy(request, pillar)

    return endpoint


def _redirect_to_slash(pillar):
    async def endpoint(request: Request) -> Response:
        return RedirectResponse(pillar.prefix + "/", status_code=307)

    return endpoint


async def shell(request: Request) -> Response:
    return FileResponse(SHELL_DIR / "index.html")


async def _get_json(port: int, path: str):
    try:
        response = await _client.get(upstream_url(port, path, ""), timeout=3.0)
        return response.json() if response.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return None


async def suite_home(request: Request) -> Response:
    """Batches waiting at each stage. Every source is best-effort: a pillar
    that is down reports null rather than failing the whole view."""
    profiles, queue = await asyncio.gather(
        _get_json(PORTS["searcher"], "api/profiles"),
        _get_json(PORTS["poster"], "api/queue"),
    )
    try:
        stages = home.handoff_stages(os.environ | suite_env.handoff_env(os.environ))
    except (KeyError, suite_env.SuiteConfigError):
        stages = {"to_clipper": None, "to_poster": None}
    search = None
    if isinstance(profiles, list):
        search = {
            "candidates": sum(int(p.get("candidates", 0)) for p in profiles),
            "selected": sum(int(p.get("selected", 0)) for p in profiles),
        }
    scheduled = None
    if isinstance(queue, dict):
        scheduled = [
            {
                "id": b.get("id"),
                "fire_time": b.get("fire_time"),
                "status": b.get("status"),
            }
            for b in queue.get("batches", [])
        ]
    return JSONResponse(
        {
            "search": search,
            "to_clipper": stages["to_clipper"],
            "to_poster": stages["to_poster"],
            "scheduled": scheduled,
        }
    )


async def suite_status(request: Request) -> Response:
    state = None
    if STATE_FILE:
        try:
            state = json.loads(Path(STATE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = None
    return JSONResponse(
        {
            "pillars": [
                {"name": p.name, "tab": p.tab, "prefix": p.prefix} for p in PILLARS
            ],
            "state": state,
        }
    )


class GuardMiddleware:
    """Refuse foreign Host headers and cross-origin state changes."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            request = Request(scope)
            host = request.headers.get("host", "")
            if host not in allowed_hosts():
                await JSONResponse({"detail": "unexpected Host"}, 421)(
                    scope, receive, send
                )
                return
            if not _origin_ok(request):
                await JSONResponse({"detail": "cross-origin request refused"}, 403)(
                    scope, receive, send
                )
                return
        await self.app(scope, receive, send)


def build_routes():
    routes = [
        Route("/", shell),
        Route("/api/suite/home", suite_home),
        Route("/api/suite/status", suite_status),
        Mount("/shell", app=StaticFiles(directory=str(SHELL_DIR)), name="shell"),
    ]
    methods = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    for pillar in PILLARS:
        routes.append(Route(pillar.prefix, _redirect_to_slash(pillar)))
        routes.append(
            Route(pillar.prefix + "/{path:path}", _proxy_route(pillar), methods=methods)
        )
    return routes


app = GuardMiddleware(Starlette(routes=build_routes()))
