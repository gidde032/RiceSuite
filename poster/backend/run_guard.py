"""Shared single-run guard: only one posting run (manual or scheduled) at a time.

Extracted from main.py so scheduler.py can use it without importing main
(circular import). Single event loop, no await between check and set, so
the flag is race-safe in asyncio.

RiceSuite's stop hold (suite ADR-001 Q17) lives here too, so manual and
scheduled runs honour it through the same check: `rice stop` takes the hold
before it signals, and no new run can start while it is set.
"""

import time

# How long a stop hold lasts if the stop that took it never happens.
STOP_HOLD_LEASE_S = 120.0

_post_running = False
_hold_until = 0.0  # time.monotonic() deadline of the stop hold; 0.0 means none


def try_acquire() -> bool:
    """Attempt to acquire the posting guard. Returns True if acquired."""
    global _post_running
    if _post_running or is_held():
        return False
    _post_running = True
    return True


def release():
    """Release the posting guard."""
    global _post_running
    _post_running = False


def is_running() -> bool:
    """Check if a posting run is active (read-only)."""
    return _post_running


def hold(lease_s: float = STOP_HOLD_LEASE_S) -> bool:
    """Refuse new posting runs for ``lease_s`` seconds. Refused, and False,
    while a run is active: that run must finish, or the stop is forced."""
    global _hold_until
    if _post_running:
        return False
    _hold_until = time.monotonic() + lease_s
    return True


def release_hold():
    """End the stop hold, e.g. when the stop that took it was refused."""
    global _hold_until
    _hold_until = 0.0


def is_held() -> bool:
    return time.monotonic() < _hold_until
