"""Whether stopping the suite is safe right now (ADR-001 Q17, SPEC FR-17/18).

Asks Poster over its own API. A posting run in progress blocks a stop unless
forced; a scheduled batch that is overdue or due soon only warns, because
Poster's startup catch-up fires it on the next start.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

import httpx

from ricesuite.ports import HOST

DUE_SOON = dt.timedelta(minutes=30)


@dataclass
class PosterState:
    reachable: bool
    active: bool = False
    due: list[dict] = field(default_factory=list)


def read_poster_state(port: int, timeout: float = 5.0) -> PosterState:
    base = f"http://{HOST}:{port}"
    try:
        with httpx.Client(timeout=timeout) as client:
            progress = client.get(f"{base}/api/post-progress").json()
            batches = client.get(f"{base}/api/queue").json().get("batches", [])
    except (httpx.HTTPError, ValueError):
        return PosterState(reachable=False)
    now = dt.datetime.now(dt.UTC)
    active = bool(progress.get("active")) or any(
        b.get("status") == "running" for b in batches
    )
    due = []
    for batch in batches:
        if batch.get("status") != "pending":
            continue
        try:
            fire = dt.datetime.fromisoformat(batch["fire_time"])
        except (KeyError, TypeError, ValueError):
            continue
        if fire.tzinfo is None:
            fire = fire.replace(tzinfo=dt.UTC)
        if fire <= now + DUE_SOON:
            due.append(batch)
    return PosterState(reachable=True, active=active, due=due)


@dataclass
class StopDecision:
    allowed: bool
    messages: list[str]


def decide(
    state: PosterState | None, poster_running: bool, force: bool
) -> StopDecision:
    """``state`` is None when Poster was not asked (its process is not running)."""
    messages: list[str] = []
    if poster_running and (state is None or not state.reachable):
        blocking = (
            "Poster is running but did not answer, so a posting run may be active."
        )
    elif state is not None and state.active:
        blocking = "A Poster posting run is in progress. Stopping now would cut it off."
    else:
        blocking = ""
    if state is not None:
        for batch in state.due:
            messages.append(
                f"warning: scheduled batch {str(batch.get('id', '?'))[:8]} is due at "
                f"{batch.get('fire_time')}. It will not fire while RiceSuite is "
                f"stopped; Poster fires it on the next start."
            )
    if blocking and not force:
        messages.append(
            f"refusing to stop: {blocking} Wait for it to finish, or use --force."
        )
        return StopDecision(False, messages)
    if blocking:
        messages.append(f"--force: stopping anyway. {blocking}")
    return StopDecision(True, messages)
