"""Clipper's data paths as ``rice data location`` reports them.

An explicit value (as ``rice start`` passes it) wins and never consults the
suite. Unset or blank, a path resolves through ``ricesuite.env.load()``, as
standalone Searcher (RiceSuite #73) and Poster (#45) do: the suite data
location after ``rice data cutover`` or on a fresh install, the legacy default
otherwise. An invalid suite configuration (for example an interrupted cutover)
raises ``ricesuite.env.SuiteConfigError``, exactly as ``rice start`` refuses it.

The three paths are resolved together, once per process: the suite tells a
fresh install from a legacy one by whether any legacy path exists, so
re-resolving later (say after the first Send created ``~/riceclipper-handoff``)
could move the work root and inbox mid-run.
"""

from __future__ import annotations

import os
from pathlib import Path

import ricesuite
from ricesuite import env as suite_env

VARIABLES = (
    "RICECLIPPER_WORK_DIR",
    "RICECLIPPER_SEARCHER_INBOX",
    "RICECLIPPER_HANDOFF_DIR",
)
_CHECKOUT = Path(__file__).resolve().parents[2]
# Resolved paths for this process; tests reset it to None.
_resolved: dict[str, Path] | None = None


def resolve(variable: str) -> Path:
    """``variable``'s directory, ``~`` expanded."""
    global _resolved
    if _resolved is None:
        _resolved = _resolve_all()
    return _resolved[variable]


def _resolve_all() -> dict[str, Path]:
    values = {name: (os.getenv(name) or "").strip() for name in VARIABLES}
    if not all(values.values()):
        # A ricesuite from another checkout would name that checkout's live
        # clipper/.riceclipper_work as the legacy work root (as Poster refuses).
        if Path(ricesuite.SUITE_ROOT).resolve() != _CHECKOUT:
            raise suite_env.SuiteConfigError(
                f"ricesuite is imported from another checkout "
                f"({ricesuite.SUITE_ROOT}). Reinstall it from {_CHECKOUT}, or set "
                f"{', '.join(VARIABLES)}."
            )
        suite = suite_env.load()
        values = {name: value or suite[name] for name, value in values.items()}
    return {name: Path(value).expanduser() for name, value in values.items()}
