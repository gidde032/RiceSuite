"""Clipper's data paths as ``rice data location`` reports them.

An explicit value (as ``rice start`` passes it) wins and never consults the
suite. Unset or blank, a path resolves through ``ricesuite.env.load()``, as
standalone Searcher (RiceSuite #73) and Poster (#45) do: the suite data
location after ``rice data cutover`` or on a fresh install, the legacy default
otherwise. An invalid suite configuration (for example an interrupted cutover)
raises ``ricesuite.env.SuiteConfigError``, exactly as ``rice start`` refuses it.
"""

from __future__ import annotations

import os
from pathlib import Path

from ricesuite import env as suite_env


def resolve(variable: str) -> Path:
    """``variable``'s directory, ``~`` expanded."""
    value = (os.getenv(variable) or "").strip()
    if not value:
        value = suite_env.load()[variable]
    return Path(value).expanduser()
