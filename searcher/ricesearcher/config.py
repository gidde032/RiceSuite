"""Local-first paths and settings (SPEC §5, §8).

Everything lives under one env-overridable data root. No cloud storage, ever.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_DATA_ENV = "RICESEARCHER_DATA_DIR"
_DEFAULT_DATA_DIR = "~/.ricesearcher"

# Mirrors RiceClipper's handoff-root convention so the shared root lines up
# (SPEC §7). RiceSearcher only ever *writes* here.
_HANDOFF_ENV = "RICESEARCHER_HANDOFF_DIR"
_DEFAULT_HANDOFF_DIR = "~/riceclipper-handoff"


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
