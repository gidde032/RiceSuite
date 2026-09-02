"""Local watch-folder acquirer (D1).

Ingests a media file the maintainer supplies by path. Zero network, no
dependencies — the always-available door when yt-dlp or a source site breaks.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.models import SourceKind

_MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav", ".aac"}


def ffprobe_duration(path: Path) -> float:
    """Return a media file's duration in seconds via ffprobe (0.0 on failure)."""
    out = subprocess.run(  # pragma: no cover - exercised via the live adapter path
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    return float(json.loads(out.stdout)["format"]["duration"])


class WatchFolderAcquirer:
    """Acquire a local media file by absolute or relative path.

    ``duration_prober`` is injectable so the wiring is testable without ffprobe;
    it defaults to :func:`ffprobe_duration`. A probe failure falls back to 0.0
    rather than failing the pull.
    """

    def __init__(
        self, duration_prober: Callable[[Path], float] = ffprobe_duration
    ) -> None:
        self._duration_prober = duration_prober

    def can_handle(self, request: str) -> bool:
        p = Path(request).expanduser()
        return p.is_file() and p.suffix.lower() in _MEDIA_SUFFIXES

    def acquire(self, request: str) -> AcquiredSource:
        path = Path(request).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(f"no such media file: {request}")
        try:
            duration = self._duration_prober(path)
        except Exception:
            # Provenance is best-effort; a missing/broken ffprobe must not fail
            # the always-available local door.
            duration = 0.0
        return AcquiredSource(
            kind=SourceKind.LOCAL,
            ref=str(path),
            media_path=path,
            title=path.stem,
            duration_s=duration,
        )
