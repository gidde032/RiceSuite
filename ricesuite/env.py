"""The single suite config file, ``ricesuite.env`` (ADR-001 Q14).

One file at the suite root replaces the three per-pillar files
(``searcher/credentials.env``, ``clipper/.env``, ``poster/credentials.env``).
It keeps every existing variable name: values are passed through to the pillar
processes unchanged, so each pillar reads exactly the variable it always read,
and one ``ANTHROPIC_API_KEY`` serves all three.

Precedence matches every pillar's own loader: a variable already exported in
the shell wins over the file.

The two handoff directories are not left to the file. :func:`handoff_env`
derives all four handoff variables from one value per stage, so the producer
and consumer of a stage cannot point at different directories.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values

from ricesuite import SUITE_ROOT

DEFAULT_ENV_FILE = SUITE_ROOT / "ricesuite.env"

# Every variable a pillar reads, by exact name. tests/test_env.py scans the
# pillar sources and fails if a pillar reads a name missing from this list, or
# if ricesuite.env.example does not document one.
SEARCHER_VARIABLES = (
    "RICESEARCHER_DATA_DIR",
    "RICESEARCHER_PROFILES_DIR",
    "RICESEARCHER_HANDOFF_DIR",
    "RICESEARCHER_SCORER_MODEL",
    "RICESEARCHER_EMBED_MODEL",
    "ANTHROPIC_AUTH_TOKEN",
)
CLIPPER_VARIABLES = (
    "RICECLIPPER_HEADER_STYLE",
    "RICECLIPPER_HEADER_MODEL",
    "RICECLIPPER_WHISPER_MODEL",
    "RICECLIPPER_WHISPER_DEVICE",
    "RICECLIPPER_WHISPER_COMPUTE",
    "RICECLIPPER_WHISPER_CPU_THREADS",
    "RICECLIPPER_FFMPEG_THREADS",
    "RICECLIPPER_HANDOFF_DIR",
    "RICECLIPPER_SEARCHER_INBOX",
    "TOKENIZERS_PARALLELISM",
)
POSTER_VARIABLES = (
    "RICEPOSTER_DATA_DIR",
    "POST_MODE",
    "HEADLESS",
    "LOG_LEVEL",
    "SCHEDULER_ENABLED",
    "ACCOUNT_SLOTS",
    "HANDOFF_DIR",
    "CLIPPER_INGEST_STYLE",
    "NOTIFY_SERVICE",
    "NTFY_TOPIC",
    "NTFY_SERVER",
    "SESSION_CHECK_TTL_S",
    "PREFLIGHT_CHECK_PLATFORMS",
    "INTER_SLOT_DELAY_MIN_S",
    "INTER_SLOT_DELAY_MAX_S",
    "FEED_DWELL_MIN_S",
    "FEED_DWELL_MAX_S",
)
SHARED_VARIABLES = ("ANTHROPIC_API_KEY",)
# Read by RiceSuite itself, never by a pillar.
SUITE_VARIABLES = ("RICESUITE_ENV",)

KNOWN_VARIABLES = frozenset(
    SUITE_VARIABLES
    + SHARED_VARIABLES
    + SEARCHER_VARIABLES
    + CLIPPER_VARIABLES
    + POSTER_VARIABLES
)

# Poster's per-account variables are named after the account slot id.
KNOWN_PATTERNS = (
    re.compile(r"^IG_ACCOUNT_[A-Za-z0-9_-]+_(NAME|ID|TOKEN)$"),
    re.compile(r"^TT_ACCOUNT_[A-Za-z0-9_-]+_TOKEN$"),
)

# The two handoff stages. Each tuple is (producer variable, consumer variable,
# default). The pillar defaults are unchanged.
SEARCHER_TO_CLIPPER = (
    "RICESEARCHER_HANDOFF_DIR",
    "RICECLIPPER_SEARCHER_INBOX",
    "~/ricesearcher-handoff",
)
CLIPPER_TO_POSTER = ("RICECLIPPER_HANDOFF_DIR", "HANDOFF_DIR", "~/riceclipper-handoff")


class SuiteConfigError(ValueError):
    """ricesuite.env (or the shell) holds a configuration the suite refuses."""


def is_known(name: str) -> bool:
    return name in KNOWN_VARIABLES or any(p.match(name) for p in KNOWN_PATTERNS)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file. A missing file is an empty config, not an error:
    every pillar runs with none of its variables set."""
    if not path.is_file():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


def unknown_keys(values: Mapping[str, str]) -> list[str]:
    """Keys no pillar reads — almost always a typo worth reporting."""
    return sorted(k for k in values if not is_known(k))


def merge(file_values: Mapping[str, str], base: Mapping[str, str]) -> dict[str, str]:
    """The environment for pillar processes: ``base`` (the shell) wins."""
    merged = dict(file_values)
    merged.update(base)
    return merged


def _stage_dir(env: Mapping[str, str], stage: tuple[str, str, str]) -> str:
    producer, consumer, default = stage
    values = {
        name: str(Path(env[name]).expanduser())
        for name in (producer, consumer)
        if env.get(name, "").strip()
    }
    if len(set(values.values())) > 1:
        raise SuiteConfigError(
            f"{producer}={env[producer]!r} and {consumer}={env[consumer]!r} "
            f"name different directories. They are two ends of one handoff; "
            f"set only {producer} and RiceSuite sets {consumer} to match."
        )
    return next(iter(values.values()), str(Path(default).expanduser()))


def handoff_env(env: Mapping[str, str]) -> dict[str, str]:
    """All four handoff variables, derived so each stage agrees with itself.

    Raises :class:`SuiteConfigError` when the two ends of a stage were set to
    different directories, or when both stages share one directory (Clipper
    reads one and writes the other; RiceSearcher and RicePoster never share a
    directory).
    """
    first = _stage_dir(env, SEARCHER_TO_CLIPPER)
    second = _stage_dir(env, CLIPPER_TO_POSTER)
    if first == second:
        raise SuiteConfigError(
            f"Both handoff stages point at {first}. Searcher→Clipper and "
            f"Clipper→Poster must use different directories."
        )
    return {
        SEARCHER_TO_CLIPPER[0]: first,
        SEARCHER_TO_CLIPPER[1]: first,
        CLIPPER_TO_POSTER[0]: second,
        CLIPPER_TO_POSTER[1]: second,
    }


def load(
    path: Path | None = None, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """The complete environment for the pillar processes.

    ``path`` defaults to ``$RICESUITE_ENV`` or ``<suite root>/ricesuite.env``;
    ``base`` defaults to ``os.environ``.
    """
    base = os.environ if base is None else base
    if path is None:
        path = Path(base.get("RICESUITE_ENV") or DEFAULT_ENV_FILE).expanduser()
    env = merge(read_env_file(path), base)
    env.update(handoff_env(env))
    return env
