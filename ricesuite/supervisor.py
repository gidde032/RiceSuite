"""Supervise the gateway and pillar processes (ADR-001 Q6, Q17).

Each child runs in its own session, so a Ctrl-C in the terminal reaches only
the launcher, which then decides whether stopping is safe. A child that exits
while the suite is running is restarted with backoff; only that child restarts.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

# Seconds to wait before each successive restart. A child that stayed up for
# STABLE_AFTER_S before crashing starts again from the first step.
BACKOFF_S: tuple[float, ...] = (1.0, 2.0, 5.0, 10.0, 30.0)
STABLE_AFTER_S = 60.0


@dataclass
class Child:
    name: str
    argv: Sequence[str]
    cwd: str
    env: Mapping[str, str]
    port: int
    proc: subprocess.Popen | None = None
    restarts: int = 0
    started_at: float = 0.0
    restart_at: float | None = None
    streak: int = 0  # consecutive quick crashes, drives the backoff step
    last_exit: int | None = None
    history: list[str] = field(default_factory=list)

    @property
    def state(self) -> str:
        if self.proc is not None and self.proc.poll() is None:
            return "running"
        if self.restart_at is not None:
            return "restarting"
        return "stopped"


class Supervisor:
    def __init__(
        self,
        children: Sequence[Child],
        log: Callable[[str], None] = print,
        clock: Callable[[], float] = time.monotonic,
        backoff: Sequence[float] = BACKOFF_S,
        on_spawn: Callable[[], None] | None = None,
    ) -> None:
        self.children = list(children)
        self.log = log
        self.clock = clock
        self.backoff = tuple(backoff)
        self.on_spawn = on_spawn  # runs after every spawn and restart
        self.stopping = False

    def _spawn(self, child: Child) -> None:
        child.proc = subprocess.Popen(
            list(child.argv),
            cwd=child.cwd,
            env=dict(child.env),
            start_new_session=True,
        )
        child.started_at = self.clock()
        child.restart_at = None
        if self.on_spawn is not None:
            self.on_spawn()

    def start_all(self) -> None:
        for child in self.children:
            self._spawn(child)
            self.log(f"started {child.name} (pid {child.proc.pid}, port {child.port})")

    def poll_once(self) -> None:
        """Restart any child that exited while the suite is running."""
        if self.stopping:
            return
        now = self.clock()
        for child in self.children:
            if child.restart_at is not None:
                if now >= child.restart_at:
                    self._spawn(child)
                    child.restarts += 1
                    self.log(
                        f"restarted {child.name} (pid {child.proc.pid}, "
                        f"restart #{child.restarts})"
                    )
                continue
            if child.proc is None:
                continue
            code = child.proc.poll()
            if code is None:
                continue
            child.last_exit = code
            if now - child.started_at >= STABLE_AFTER_S:
                child.streak = 0
            delay = self.backoff[min(child.streak, len(self.backoff) - 1)]
            child.streak += 1
            child.restart_at = now + delay
            child.history.append(f"exit {code}")
            self.log(f"{child.name} exited with code {code}; restarting in {delay:g}s")

    def stop_all(self, timeout: float = 15.0) -> None:
        """Terminate every child (SIGTERM, then SIGKILL after ``timeout``)."""
        self.stopping = True
        live = [
            c for c in self.children if c.proc is not None and c.proc.poll() is None
        ]
        for child in live:
            try:
                os.killpg(child.proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + timeout
        for child in live:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                child.proc.wait(remaining)
            except subprocess.TimeoutExpired:
                self.log(f"{child.name} did not stop in {timeout:g}s; killing it")
                try:
                    os.killpg(child.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.proc.wait()
        for child in self.children:
            child.restart_at = None

    def snapshot(self) -> dict[str, dict]:
        return {
            c.name: {
                "state": c.state,
                "pid": c.proc.pid if c.proc is not None else None,
                "port": c.port,
                "restarts": c.restarts,
                "last_exit": c.last_exit,
            }
            for c in self.children
        }
