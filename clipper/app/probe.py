"""ffprobe / ffmpeg capability helpers.

Includes the libass capability check: RiceClipper's caption + header burn-in
(SPEC.md §5-6) requires an ffmpeg built with libass (the ``subtitles`` filter).
Homebrew's stock ``ffmpeg`` formula does NOT include it; the
``homebrew-ffmpeg/ffmpeg/ffmpeg`` tap build does.
We surface this loudly at startup so a missing toolchain fails clearly instead of
producing a caption-less clip.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    width: int
    height: int
    duration: float
    has_audio: bool


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def has_libass() -> bool:
    """True if this ffmpeg exposes the libass-backed ``subtitles`` filter."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        out = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except (subprocess.CalledProcessError, OSError):
        return False
    return any(line.split()[1:2] == ["subtitles"] for line in out.splitlines() if line.strip())


def _pick_duration(video: dict, fmt: dict) -> float:
    """First parseable duration from the video stream then the container format."""
    for src in (video.get("duration"), fmt.get("duration")):
        try:
            return float(src)
        except (TypeError, ValueError):
            continue
    return 0.0


def probe(path: str) -> MediaInfo:
    """Return dimensions, duration and audio presence for ``path``."""
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                "-show_format",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    except subprocess.CalledProcessError as exc:  # pragma: no cover - passthrough
        raise ProbeError(exc.stderr.strip() or "ffprobe failed") from exc

    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no video stream found")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = _pick_duration(video, data.get("format", {}))
    if duration <= 0.0:
        # A 0-duration would make the header a zero-length (invisible) event.
        raise ProbeError("could not determine video duration")

    return MediaInfo(
        width=int(video["width"]),
        height=int(video["height"]),
        duration=duration,
        has_audio=has_audio,
    )
