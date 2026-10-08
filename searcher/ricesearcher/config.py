"""Local-first paths and settings (SPEC §5, §8).

Everything lives under one env-overridable data root. No cloud storage, ever.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from ricesuite import env as suite_env

# Unset or blank, the data root and handoff root are what `rice data location`
# reports (#73): ``<RICESUITE_DATA_DIR>/searcher`` after a cutover or on a fresh
# install, ``~/.ricesearcher`` on a legacy one. ricesuite.env supplies them, so
# the standalone CLI and `rice start` never disagree.
_DATA_ENV = "RICESEARCHER_DATA_DIR"

# RiceSearcher's OWN handoff root (SPEC §7). It only ever *writes* here; RiceClipper
# reads from it and writes its rendered output to the SEPARATE ~/riceclipper-handoff
# (where RicePoster pulls). RiceSearcher and RicePoster never share a directory —
# RiceClipper is the intermediary. Do NOT point this at ~/riceclipper-handoff.
_HANDOFF_ENV = "RICESEARCHER_HANDOFF_DIR"

# Saved beat profiles (ADR-002). Defaults under the data root; override the
# directory with this env var (in the shell or ricesuite.env).
_PROFILES_ENV = "RICESEARCHER_PROFILES_DIR"


@dataclass(frozen=True)
class Config:
    """Resolved local paths for one RiceSearcher instance."""

    data_dir: Path
    handoff_dir: Path
    # RICESEARCHER_PROFILES_DIR from ricesuite.env; the shell still wins.
    profiles_override: Path | None = None

    @property
    def db_path(self) -> Path:
        """SQLite library index (SPEC §6)."""
        return self.data_dir / "library.sqlite3"

    @property
    def cache_dir(self) -> Path:
        """Content-addressed media cache (source video + extracted clips)."""
        return self.data_dir / "cache"

    @property
    def profiles_dir(self) -> Path:
        """Saved beat profiles (ADR-002). Env-overridable directory."""
        override = (os.getenv(_PROFILES_ENV) or "").strip()
        if override:
            return Path(override).expanduser()
        if self.profiles_override is not None:
            return self.profiles_override
        return self.data_dir / "profiles"

    def ensure_dirs(self) -> None:
        """Create the data + cache dirs if missing (idempotent)."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """Resolve paths as the RiceSuite launcher would, expanding ``~``.

    The shell wins, then ricesuite.env, then the suite data location. The suite
    is consulted only when a path is unset or blank, so explicit paths (as
    `rice start` passes them) never depend on the rest of the suite config;
    only ricesuite.env's RICESEARCHER_PROFILES_DIR is still read for them.
    Otherwise raises ``ricesuite.env.SuiteConfigError`` when the suite
    configuration is invalid (for example an interrupted cutover), exactly as
    `rice start` refuses it.
    """
    data = (os.getenv(_DATA_ENV) or "").strip()
    handoff = (os.getenv(_HANDOFF_ENV) or "").strip()
    if data and handoff:
        # `rice start` still passes ricesuite.env's profiles directory (a
        # custom-profiles cutover files it there); read only that, unvalidated.
        path = Path(os.getenv("RICESUITE_ENV") or suite_env.DEFAULT_ENV_FILE)
        filed = suite_env.read_env_file(path.expanduser())
    else:
        filed = suite_env.load()
        data, handoff = filed[_DATA_ENV], filed[_HANDOFF_ENV]
    profiles = filed.get(_PROFILES_ENV, "").strip()
    return Config(
        data_dir=Path(data).expanduser(),
        handoff_dir=Path(handoff).expanduser(),
        profiles_override=Path(profiles).expanduser() if profiles else None,
    )


# Dotenv-style files loaded (from the current directory) before a command runs,
# so secrets like ANTHROPIC_API_KEY can live in a gitignored file instead of the
# shell. Both are gitignored; only the tracked ``.example`` is committed.
_ENV_FILES = ("credentials.env", ".env")


def load_env_files() -> None:
    """Load ``KEY=VALUE`` lines from local env files into ``os.environ``.

    Never overrides a variable already set in the real environment (an explicit
    ``export`` wins), and silently ignores blank lines, comments, and malformed
    lines. Zero dependencies — this is not a full dotenv implementation.
    """
    for name in _ENV_FILES:
        path = Path(name)
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            if key:
                os.environ.setdefault(key, value.strip().strip('"').strip("'"))
