"""Clip extraction for the handoff (FR-9).

Trims a source's reviewed target window into a standalone clip file that
travels in the handoff batch. The ffmpeg call is behind a protocol so the writer
is testable with subprocess/probe doubles and a live ffmpeg regression. No network,
no posting — just a local ffmpeg trim.
"""

from __future__ import annotations

import math
import subprocess
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from ricesearcher.acquire.watchfolder import ffprobe_duration

_SEEK_PREROLL_S = 10.0


class ClipExtractError(RuntimeError):
    """An ffmpeg trim ran but produced no usable output clip.

    Defined here (not as a ``HandoffError``) to avoid a circular import with the
    writer; the writer lets it propagate and the web layer maps it to a 503 so a
    failed extraction is retryable rather than a raw 500 (review finding C).
    """


def _format_seconds(value: float) -> str:
    """Format a timestamp for ffmpeg without scientific notation or rounding."""
    return format(Decimal(str(value)), "f")


class ClipExtractor(Protocol):
    def extract(self, source: Path, start: float, end: float, dest: Path) -> float:
        """Write the ``[start, end]`` span of ``source`` to ``dest``.

        Returns the measured duration of the written clip (seconds), so the
        caller records what was actually produced rather than what was requested
        (review finding B1).
        """
        ...


class FfmpegClipExtractor:
    """Precisely trim a reviewed clip interval for Clipper.

    A coarse input seek followed by accurate output seeking and re-encoding
    avoids stream-offset drift without decoding a long source from the start.
    """

    def extract(
        self, source: Path, start: float, end: float, dest: Path
    ) -> float:  # pragma: no cover - subprocess/live path
        duration = max(0.0, end - start)
        coarse_seek = max(0.0, start - _SEEK_PREROLL_S)
        precise_seek = start - coarse_seek
        command = ["ffmpeg", "-y", "-loglevel", "error"]
        if coarse_seek:
            command.extend(["-ss", _format_seconds(coarse_seek)])
        command.extend(
            [
                "-i",
                str(source),
                "-ss",
                _format_seconds(precise_seek),
                "-map",
                "0:v:0?",
                "-map",
                "0:a:0?",
                "-t",
                _format_seconds(duration),
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
        try:
            output_duration = float(ffprobe_duration(dest))
        except Exception as exc:
            raise ClipExtractError("ffmpeg output could not be probed") from exc
        if not math.isfinite(output_duration) or output_duration <= 0:
            raise ClipExtractError("ffmpeg output has no usable duration")
        return output_duration
