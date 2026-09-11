"""Grab an early still frame from a clip as a base64 JPEG.

The Wave-1 auto-header (SPEC.md §6.2) sends the model a frame snapshot alongside
the transcript so it can ground the header in what the clip actually shows — the
only automatic signal on a music-only clip with no transcript (SPEC.md §9,
"early frame, ~1s in"). This is the sole producer of that snapshot; it writes a
temporary JPEG into the job dir and returns its base64, downscaled to keep the
API payload small.
"""

from __future__ import annotations

import base64
from contextlib import suppress
from pathlib import Path

from app.probe import MediaInfo
from app.process import ProcessTimeoutError, run_owned

FRAME_NAME = "header_frame.jpg"
# Bound the long edge so the uploaded frame stays a few tens of KB, not MBs.
_MAX_EDGE = 768
_FRAME_TIMEOUT_S = 30.0


class FrameGrabError(RuntimeError):
    """The early-frame snapshot could not be produced."""


def _seek_seconds(info: MediaInfo | None) -> float:
    """Pick an early, in-bounds seek point (~1s, or the midpoint of a tiny clip)."""
    duration = info.duration if info else 0.0
    if duration and duration > 0:
        return min(1.0, duration / 2.0)
    return 0.0


def grab_frame_b64(
    source_path: str | Path,
    info: MediaInfo | None,
    work_dir: str | Path,
) -> str:
    """Extract an early frame from ``source_path`` and return it as base64 JPEG.

    Raises :class:`FrameGrabError` on any failure so the caller can degrade to a
    transcript-only header instead of failing the request.
    """
    source = Path(source_path)
    out_path = Path(work_dir) / FRAME_NAME
    seek = _seek_seconds(info)
    args = [
        "ffmpeg",
        "-y",
        "-ss",
        str(seek),
        "-i",
        str(source),
        "-frames:v",
        "1",
        # Downscale only when wider than the cap; keep even height for the encoder.
        "-vf",
        f"scale='min({_MAX_EDGE},iw)':-2",
        "-q:v",
        "3",
        str(out_path),
    ]
    try:
        try:
            run_owned(
                args,
                cwd=work_dir,
                capture_output=True,
                text=True,
                timeout=_FRAME_TIMEOUT_S,
                check=True,
            )
        except ProcessTimeoutError as exc:
            raise FrameGrabError("frame grab timed out") from exc
        except Exception as exc:
            raise FrameGrabError("frame grab failed") from exc

        try:
            data = out_path.read_bytes()
        except OSError as exc:
            raise FrameGrabError("frame file was not written") from exc
        if not data:
            raise FrameGrabError("frame file was empty")
        return base64.b64encode(data).decode("ascii")
    finally:
        # The caller receives the encoded bytes, never the file path.  Keep this
        # producer-owned snapshot out of the durable job cache on every normal
        # and interrupting exit; a cleanup failure must not mask the real result.
        with suppress(OSError):
            out_path.unlink(missing_ok=True)
