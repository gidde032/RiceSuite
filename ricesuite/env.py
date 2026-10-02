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

import json
import os
import re
import sys
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
    "IG_UPLOAD_TIMEOUT_S",
    "TT_UPLOAD_TIMEOUT_S",
)
SHARED_VARIABLES = ("ANTHROPIC_API_KEY",)
# Read by the pillars but set by the launcher itself, so a value in
# ricesuite.env is ignored.
LAUNCHER_VARIABLES = ("RICESUITE_GATEWAY_PORT",)
# Read by RiceSuite itself, never by a pillar.
SUITE_VARIABLES = (
    "RICESUITE_ENV",
    "RICESUITE_RUN_DIR",
    "RICESUITE_DATA_DIR",
    "RICECLIPPER_WORK_DIR",
)

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
    return sorted(k for k in values if not is_known(k) and k not in LAUNCHER_VARIABLES)


def launcher_keys(values: Mapping[str, str]) -> list[str]:
    """Keys the launcher sets itself, overriding the file."""
    return sorted(k for k in values if k in LAUNCHER_VARIABLES)


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
    live_environment = base is None
    base = os.environ if base is None else base
    if path is None:
        path = Path(base.get("RICESUITE_ENV") or DEFAULT_ENV_FILE).expanduser()
    env = merge(read_env_file(path), base)
    env.update(
        data_env(env, legacy_present=_legacy_present() if live_environment else False)
    )
    env.update(handoff_env(env))
    return env


DATA_PATHS = {
    "RICESEARCHER_DATA_DIR": ("searcher", "~/.ricesearcher"),
    "RICECLIPPER_WORK_DIR": ("clipper", str(SUITE_ROOT / "clipper/.riceclipper_work")),
    "RICEPOSTER_DATA_DIR": ("poster", str(SUITE_ROOT / "poster")),
    "RICESEARCHER_HANDOFF_DIR": (
        "handoff/searcher-to-clipper",
        "~/ricesearcher-handoff",
    ),
    "RICECLIPPER_HANDOFF_DIR": ("handoff/clipper-to-poster", "~/riceclipper-handoff"),
}


def data_root(env: Mapping[str, str]) -> Path:
    raw = env.get("RICESUITE_DATA_DIR") or "~/.ricesuite"
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise SuiteConfigError(
            "RICESUITE_DATA_DIR must be an absolute path or start with ~"
        )
    return path


def prepare_poster_dir(env: Mapping[str, str]) -> str:
    """Poster's data directory from a loaded environment, ready to use.

    Shared by the launcher and Poster's own config (#45), so every Poster
    entry point writes where `rice start` would. The unified ``<root>/poster``
    is created on first use, because Poster refuses a missing data root. A
    symlinked unified root is refused.
    """
    poster = env["RICEPOSTER_DATA_DIR"]
    root = data_root(env)
    if Path(poster) == root / "poster":
        if root.is_symlink() or (root / "poster").is_symlink():
            raise SuiteConfigError("refusing a symlinked unified data root")
        (root / "poster").mkdir(parents=True, exist_ok=True, mode=0o700)
    return poster


def _legacy_present(
    suite_root: Path = SUITE_ROOT, *, under_pytest: bool | None = None
) -> bool:
    """Detect old installs without creating or opening any data files."""
    if under_pytest is None:
        under_pytest = "pytest" in sys.modules
    home = Path.home()
    paths = [
        home / ".ricesearcher",
        home / "ricesearcher-handoff",
        home / "riceclipper-handoff",
    ]
    if not under_pytest:
        paths.extend(
            [
                suite_root / "clipper/.riceclipper_work",
                suite_root / "poster/queue.jsonl",
                suite_root / "poster/history.jsonl",
                suite_root / "poster/sessions",
                suite_root / "poster/queue_media",
                suite_root / "poster/debug",
                suite_root / "poster/.post-in-flight.json",
            ]
        )
        media = suite_root / "poster/media"
        if media.is_dir() and any(p.name != ".gitkeep" for p in media.iterdir()):
            return True
    return any(path.exists() for path in paths)


def data_env(env: Mapping[str, str], *, legacy_present: bool = False) -> dict[str, str]:
    """Use unified defaults for fresh installs and completed cutovers.

    An existing installation keeps all legacy defaults until an explicit
    migration writes the cutover marker. Explicit pillar paths always win.
    """
    root = data_root(env)
    marker = root / ".cutover.json"
    if marker.exists() or marker.is_symlink():
        if marker.is_symlink():
            raise SuiteConfigError(f"cutover marker is a symlink: {marker}")
        try:
            state = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise SuiteConfigError(f"invalid cutover marker: {marker}") from exc
        if state.get("version") != 1 or not isinstance(state.get("digest"), str):
            raise SuiteConfigError(f"invalid cutover marker: {marker}")
        if state.get("phase") == "pending":
            raise SuiteConfigError(
                "data cutover was interrupted; stop the suite and rerun "
                "`rice data cutover`"
            )
        unified = True
    else:
        unified = not legacy_present
    paths: dict[str, str] = {}
    for variable, (suffix, legacy) in DATA_PATHS.items():
        consumer = {
            "RICESEARCHER_HANDOFF_DIR": "RICECLIPPER_SEARCHER_INBOX",
            "RICECLIPPER_HANDOFF_DIR": "HANDOFF_DIR",
        }.get(variable)
        paths[variable] = (
            env.get(variable, "").strip()
            or (env.get(consumer, "").strip() if consumer else "")
            or str(root / suffix if unified else Path(legacy).expanduser())
        )
    if not unified and env.get("RICESUITE_DATA_DIR"):
        # An explicit root is a selection, not an implicit migration.
        raise SuiteConfigError(
            "legacy data exists; run `rice data plan` and migrate before selecting "
            "RICESUITE_DATA_DIR"
        )
    selected = [
        Path(value).expanduser().resolve(strict=False) for value in paths.values()
    ]
    for i, left in enumerate(selected):
        for right in selected[i + 1 :]:
            if left == right or left in right.parents or right in left.parents:
                raise SuiteConfigError(f"data paths overlap: {left} and {right}")
    return paths
