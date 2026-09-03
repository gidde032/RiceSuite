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


class ClipExtractor(Protocol):
    def extract(self, source: Path, start: float, end: float, dest: Path) -> None:
        """Write the ``[start, end]`` second span of ``source`` to ``dest``."""
        ...


class FfmpegClipExtractor:
    """Trim a clip with ffmpeg (stream copy — fast; Clipper tightens the cut)."""

    def extract(
        self, source: Path, start: float, end: float, dest: Path
    ) -> None:  # pragma: no cover - subprocess/live path
        duration = max(0.0, end - start)
        subprocess.run(
            [
                "ffmpeg", "-y", "-loglevel", "error",
                "-ss", f"{start:.3f}", "-i", str(source),
                "-t", f"{duration:.3f}", "-c", "copy",
                str(dest),
            ],
            check=True,
        )
