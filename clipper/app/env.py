"""Load the repo-root ``.env`` into the process environment (Issue #2).

``app.main`` calls ``load_dotenv_file()`` before importing any module that reads
settings at import time (``transcribe.whisper``), so a bare
``uvicorn app.main:app`` picks up ``.env``. Variables already set in the
environment win; ``.env`` only fills gaps. Local file read only — no network.
"""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

DEFAULT_DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"
# Module-level so tests can redirect it before ``app.main`` is imported.
DOTENV_PATH = DEFAULT_DOTENV_PATH


def load_dotenv_file(path: Path | None = None) -> bool:
    """Load ``path`` (default ``DOTENV_PATH``) without overriding set variables.

    Returns True if the file existed and was loaded.
    """
    path = DOTENV_PATH if path is None else path
    if not path.is_file():
        return False
    return load_dotenv(path, override=False)
