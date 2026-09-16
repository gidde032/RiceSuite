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
import math
import shutil
import subprocess
from dataclasses import dataclass

from app.process import ProcessTimeoutError, run_owned


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    # ``width``/``height`` describe the displayed, square-pixel frame used by
    # detection and filtering. The coded dimensions and metadata are retained
    # so every consumer can reason about the same normalization boundary.
    width: int
    height: int
    duration: float
    has_audio: bool
    coded_width: int | None = None
    coded_height: int | None = None
    rotation: int = 0
    sample_aspect_ratio: float = 1.0
    field_order: str = "progressive"


def _ratio(value: object) -> float:
    """Parse an ffprobe ratio, returning square pixels for invalid metadata."""
    try:
        numerator, denominator = str(value or "1:1").split(":", 1)
        ratio = float(numerator) / float(denominator)
    except (TypeError, ValueError, ZeroDivisionError):
        return 1.0
    return ratio if math.isfinite(ratio) and ratio > 0 else 1.0


def _rotation(video: dict) -> int:
    """Return ffprobe's display rotation normalized to [0, 360)."""
    for side_data in video.get("side_data_list", []):
        try:
            return round(float(side_data["rotation"])) % 360
        except (KeyError, TypeError, ValueError):
            continue
    try:
        return round(float(video.get("tags", {})["rotate"])) % 360
    except (KeyError, TypeError, ValueError):
        pass
    return 0


def _even(value: float) -> int:
    rounded = max(2, round(value))
    return rounded if rounded % 2 == 0 else rounded + 1


def _probe_timeout() -> float:
    """Return the bounded timeout for ffprobe and capability checks."""
    return 30.0


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def has_libass() -> bool:
    """True if this ffmpeg exposes the libass-backed ``subtitles`` filter."""
    if shutil.which("ffmpeg") is None:
        return False
    try:
        out = run_owned(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True,
            text=True,
            timeout=_probe_timeout(),
            check=True,
        ).stdout
    except ProcessTimeoutError as exc:
        raise ProbeError(
            f"ffmpeg libass probe timed out after {_probe_timeout():g} seconds"
        ) from exc
    except (subprocess.CalledProcessError, OSError):
        return False
    return any(
        line.split()[1:2] == ["subtitles"] for line in out.splitlines() if line.strip()
    )


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
        out = run_owned(
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
            timeout=_probe_timeout(),
            check=True,
        ).stdout
    except ProcessTimeoutError as exc:
        raise ProbeError(
            f"ffprobe timed out after {_probe_timeout():g} seconds"
        ) from exc
    except subprocess.CalledProcessError as exc:  # pragma: no cover - passthrough
        raise ProbeError(exc.stderr.strip() or "ffprobe failed") from exc

    data = json.loads(out)
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise ProbeError("no video stream found")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = _pick_duration(video, data.get("format", {}))
    if not math.isfinite(duration) or duration <= 0.0:
        # A non-positive/non-finite duration cannot safely bound a render.
        raise ProbeError("could not determine video duration")

    coded_width = int(video["width"])
    coded_height = int(video["height"])
    rotation = _rotation(video)
    sar = _ratio(video.get("sample_aspect_ratio"))
    quarter_turn = rotation in {90, 270}
    pixel_width, pixel_height = (
        (coded_height, coded_width) if quarter_turn else (coded_width, coded_height)
    )
    display_sar = 1.0 / sar if quarter_turn else sar

    return MediaInfo(
        width=_even(pixel_width * display_sar),
        height=_even(pixel_height),
        duration=duration,
        has_audio=has_audio,
        coded_width=coded_width,
        coded_height=coded_height,
        rotation=rotation,
        sample_aspect_ratio=sar,
        field_order=str(video.get("field_order") or "progressive"),
    )
