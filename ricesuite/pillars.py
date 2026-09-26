"""How each process of the suite is started (ADR-001 Q6)."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from ricesuite import SUITE_ROOT
from ricesuite.ports import GATEWAY_PORT, HOST, PILLAR_PORTS


@dataclass(frozen=True)
class Pillar:
    name: str  # searcher / clipper / poster
    tab: str  # label on the shell's top bar
    prefix: str  # gateway path prefix, e.g. "/search"
    directory: str  # working directory, relative to the suite root
    target: str  # uvicorn app
    factory: bool = False
    uvicorn_flags: tuple[str, ...] = ()


PILLARS: tuple[Pillar, ...] = (
    Pillar(
        "searcher",
        "Search",
        "/search",
        "searcher",
        "ricesearcher.web.app:create_app",
        factory=True,
        # `ricesearcher review` runs its server at warning level.
        uvicorn_flags=("--log-level", "warning"),
    ),
    Pillar("clipper", "Clip", "/clip", "clipper", "app.main:app"),
    Pillar(
        "poster",
        "Post",
        "/post",
        "poster",
        "backend.main:app",
        # RicePoster ran on plain uvicorn: asyncio + h11. The shared venv has
        # uvicorn[standard] (Clipper's pin), whose auto-detection would switch
        # Poster to uvloop/httptools under its scheduler and Playwright. Pin the
        # originals. Never --reload: it would kill an in-flight post.
        uvicorn_flags=("--loop", "asyncio", "--http", "h11"),
    ),
)

BY_NAME = {p.name: p for p in PILLARS}


def uvicorn_argv(
    target: str, port: int, factory: bool = False, flags: tuple[str, ...] = ()
) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "uvicorn",
        target,
        "--host",
        HOST,
        "--port",
        str(port),
    ]
    if factory:
        argv.append("--factory")
    return argv + list(flags)


def pillar_argv(pillar: Pillar, port: int | None = None) -> list[str]:
    port = PILLAR_PORTS[pillar.name] if port is None else port
    return uvicorn_argv(pillar.target, port, pillar.factory, pillar.uvicorn_flags)


def gateway_argv(port: int = GATEWAY_PORT) -> list[str]:
    return uvicorn_argv("ricesuite.gateway:app", port, flags=("--log-level", "warning"))


def pillar_cwd(pillar: Pillar) -> str:
    return str(SUITE_ROOT / pillar.directory)
