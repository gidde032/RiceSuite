"""Clip extraction for the handoff (FR-9).

Trims a source's padded window [pad_in, pad_out] into a standalone clip file that
travels in the handoff batch. The ffmpeg call is behind a protocol so the writer
is testable with a fake extractor; the real one is lazy/subprocess and untested
(``# pragma: no cover``). No network, no posting — just a local ffmpeg trim.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Protocol

_SEEK_PREROLL_S = 10.0


class ClipExtractor(Protocol):
    def extract(self, source: Path, start: float, end: float, dest: Path) -> None:
        """Write the ``[start, end]`` second span of ``source`` to ``dest``."""
        ...


class FfmpegClipExtractor:
    """Precisely trim a short padded clip for Clipper.

    A coarse input seek followed by accurate output seeking and re-encoding
    avoids stream-offset drift without decoding a long source from the start.
    """

    def extract(
        self, source: Path, start: float, end: float, dest: Path
    ) -> None:  # pragma: no cover - subprocess/live path
        duration = max(0.0, end - start)
        coarse_seek = max(0.0, start - _SEEK_PREROLL_S)
        precise_seek = start - coarse_seek
        command = ["ffmpeg", "-y", "-loglevel", "error"]
        if coarse_seek:
            command.extend(["-ss", f"{coarse_seek:.3f}"])
        command.extend(
            [
                "-i",
                str(source),
                "-ss",
                f"{precise_seek:.3f}",
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-t",
                f"{duration:.3f}",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(dest),
            ]
        )
        subprocess.run(
            command,
            check=True,
        )
