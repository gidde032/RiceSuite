"""Local watch-folder acquirer (D1).

Ingests a media file the maintainer supplies by path. Zero network, no
dependencies — the always-available door when yt-dlp or a source site breaks.
"""

from __future__ import annotations

from pathlib import Path

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.models import SourceKind

_MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".aac"}


class WatchFolderAcquirer:
    """Acquire a local media file by absolute or relative path."""

    def can_handle(self, request: str) -> bool:
        p = Path(request).expanduser()
        return p.is_file() and p.suffix.lower() in _MEDIA_SUFFIXES

    def acquire(self, request: str) -> AcquiredSource:
        path = Path(request).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no such media file: {request}")
        return AcquiredSource(
            kind=SourceKind.LOCAL,
            ref=str(path),
            media_path=path,
            title=path.stem,
        )
