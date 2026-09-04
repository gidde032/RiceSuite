"""Local-first paths and settings (SPEC §5, §8).

Everything lives under one env-overridable data root. No cloud storage, ever.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DATA_ENV = "RICESEARCHER_DATA_DIR"
_DEFAULT_DATA_DIR = "~/.ricesearcher"

# RiceSearcher's OWN handoff root (SPEC §7). It only ever *writes* here; RiceClipper
# reads from it and writes its rendered output to the SEPARATE ~/riceclipper-handoff
# (where RicePoster pulls). RiceSearcher and RicePoster never share a directory —
# RiceClipper is the intermediary. Do NOT point this at ~/riceclipper-handoff.
_HANDOFF_ENV = "RICESEARCHER_HANDOFF_DIR"
_DEFAULT_HANDOFF_DIR = "~/ricesearcher-handoff"


@dataclass(frozen=True)
class Config:
    """Resolved local paths for one RiceSearcher instance."""

    data_dir: Path
    handoff_dir: Path

    @property
    def db_path(self) -> Path:
        """SQLite library index (SPEC §6)."""
        return self.data_dir / "library.sqlite3"

    @property
    def cache_dir(self) -> Path:
        """Content-addressed media cache (source video + extracted clips)."""
        return self.data_dir / "cache"

    def ensure_dirs(self) -> None:
        """Create the data + cache dirs if missing (idempotent)."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.cache_dir.mkdir(parents=True, exist_ok=True)


def load_config() -> Config:
    """Resolve config from the environment, expanding ``~``."""
    data_dir = Path(os.getenv(_DATA_ENV) or _DEFAULT_DATA_DIR).expanduser()
    handoff_dir = Path(os.getenv(_HANDOFF_ENV) or _DEFAULT_HANDOFF_DIR).expanduser()
    return Config(data_dir=data_dir, handoff_dir=handoff_dir)


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
