"""The front door: one localhost port for the whole suite (ADR-001 Q6, Q11).

* ``/`` serves the Slate shell (top bar with Search / Clip / Post tabs and a
  home view).
* ``/search/…``, ``/clip/…``, ``/post/…`` are proxied to that pillar with the
  prefix stripped. Each pillar page uses relative URLs, so its API calls stay
  under its own prefix and reach its own pillar.
* ``/api/suite/…`` is the gateway's own read-only API for the shell.

The gateway adds no capability a pillar did not already expose on localhost.
It runs the same Host/Origin guard as every pillar listener
(``ricesuite.localguard``, SPEC FR-3), for its own configured port.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime
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

from ricesuite.localguard import LocalGuard
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

# No read timeout: Poster's Post All request stays open for the whole run.
_client = httpx.AsyncClient(
    timeout=httpx.Timeout(connect=5.0, read=None, write=None, pool=None),
    follow_redirects=False,
)


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
    """Read-only stage counts, including batches already in Clipper's workspace.

    Every source is best-effort: an unavailable source reports null. Only
    pending Poster queue entries are upcoming scheduled batches.
    """
    profiles, clip_inbox, clip_workspace, post_inbox, queue = await asyncio.gather(
        _get_json(PORTS["searcher"], "api/profiles"),
        _get_json(PORTS["clipper"], "api/searcher-inbox"),
        _get_json(PORTS["clipper"], "api/workspace-batches"),
        _get_json(PORTS["poster"], "api/handoff/inbox"),
        _get_json(PORTS["poster"], "api/queue"),
    )
    to_clipper = clip_inbox.get("batches") if isinstance(clip_inbox, dict) else None
    clip_open = (
        clip_workspace.get("batches") if isinstance(clip_workspace, dict) else None
    )
    to_poster = None
    if isinstance(post_inbox, dict):
        to_poster = list(post_inbox.get("batches") or [])
        if post_inbox.get("unacknowledged"):
            to_poster.insert(
                0, {"batch_id": post_inbox["unacknowledged"], "clip_count": None}
            )
    search = None
    if isinstance(profiles, list):
        search = {
            "candidates": sum(int(p.get("candidates", 0)) for p in profiles),
            "selected": sum(int(p.get("selected", 0)) for p in profiles),
        }
    scheduled = None
    if isinstance(queue, dict):

        def fire_time(batch):
            try:
                value = datetime.fromisoformat(batch["fire_time"])
                if value.tzinfo is not None:
                    return value.astimezone(UTC)
            except (KeyError, TypeError, ValueError):
                pass
            return datetime.max.replace(tzinfo=UTC)

        scheduled = [
            {
                "id": b.get("id"),
                "fire_time": b.get("fire_time"),
                "status": b.get("status"),
            }
            for b in sorted(
                (b for b in queue.get("batches", []) if b.get("status") == "pending"),
                key=fire_time,
            )
        ]
    return JSONResponse(
        {
            "search": search,
            "to_clipper": to_clipper,
            "clip_open": clip_open,
            "to_poster": to_poster,
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


app = LocalGuard(Starlette(routes=build_routes()), port=GATEWAY)
