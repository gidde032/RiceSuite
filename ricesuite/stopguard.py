"""Whether stopping the suite is safe right now (ADR-001 Q17, SPEC FR-17/18).

Before a stop, the launcher asks Poster for its stop hold. While the hold is
set, Poster refuses every new posting run, manual or scheduled, so no run can
start between this check and the stop. Poster refuses the hold while a run is
active, and a run in progress blocks a stop unless forced. Any answer that is
not the expected one counts as "cannot confirm idle". A scheduled batch that
is overdue or due soon only warns, because Poster's startup catch-up fires it
on the next start.
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
    held: bool = False
    due: list[dict] = field(default_factory=list)


def _json_object(response: httpx.Response) -> dict:
    response.raise_for_status()
    body = response.json()
    if not isinstance(body, dict):
        raise ValueError("expected a JSON object")
    return body


def hold_poster(port: int, timeout: float = 5.0) -> PosterState:
    """Take Poster's stop hold, then read its queue. If the queue cannot be
    read, the hold is released and Poster counts as unconfirmed."""
    base = f"http://{HOST}:{port}"
    held = False
    try:
        with httpx.Client(timeout=timeout) as client:
            answer = _json_object(client.post(f"{base}/api/stop-hold"))
            held, active = answer.get("held"), answer.get("active")
            if not isinstance(held, bool) or not isinstance(active, bool):
                held = False
                raise ValueError("stop-hold answer lacks held/active")
            batches = _json_object(client.get(f"{base}/api/queue")).get("batches")
            if not isinstance(batches, list) or not all(
                isinstance(b, dict) for b in batches
            ):
                raise ValueError("queue answer lacks a list of batches")
    except (httpx.HTTPError, ValueError):
        if held:
            release_poster(port)
        return PosterState(reachable=False)
    now = dt.datetime.now(dt.UTC)
    active = active or any(b.get("status") == "running" for b in batches)
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
    return PosterState(reachable=True, active=active, held=held, due=due)


def release_poster(port: int, timeout: float = 2.0) -> None:
    """Release Poster's stop hold. Best effort: the hold's lease ends it anyway."""
    try:
        httpx.delete(f"http://{HOST}:{port}/api/stop-hold", timeout=timeout)
    except httpx.HTTPError:
        pass


@dataclass
class StopDecision:
    allowed: bool
    messages: list[str]
    # Poster's port while this decision holds its stop hold. A stop that then
    # does not happen must release it (stopguard.release_poster).
    held_port: int | None = None


def decide(
    state: PosterState | None, poster_running: bool, force: bool
) -> StopDecision:
    """``state`` is None when Poster was not asked (its process is not running)."""
    messages: list[str] = []
    if poster_running and (state is None or not state.reachable):
        blocking = (
            "Poster is running but its answer did not confirm it is idle, so a "
            "posting run may be active."
        )
    elif state is not None and (state.active or not state.held):
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
