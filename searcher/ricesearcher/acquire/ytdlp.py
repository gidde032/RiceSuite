"""YouTube acquirer via yt-dlp (D1).

``yt_dlp`` is imported lazily inside ``acquire`` so the core package and its
tests do not require it installed. This adapter only ever *downloads* source
media for local extraction — it never authenticates to or posts on any account.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import date, datetime
from pathlib import Path

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.models import SourceKind

_URL_MARKERS = ("http://", "https://", "www.", "youtube.com", "youtu.be")


def _normalize_upload_date(value: object) -> str:
    """Return upload metadata as ISO date, or empty when it is unusable.

    Reviewer lens: provenance metadata normalization (MEDIUM). yt-dlp exposes
    YouTube's upload date as ``YYYYMMDD``; normalizing it here keeps persisted
    source metadata consistent without inventing a date for missing or invalid
    values.
    """
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if len(raw) == 8 and raw.isdigit():
        try:
            return datetime.strptime(raw, "%Y%m%d").date().isoformat()
        except ValueError:
            return ""
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError:
        return ""


class YtDlpAcquirer:
    """Acquire a video by URL (or, later, channel/search) using yt-dlp."""

    def __init__(self, download_dir: Path | None = None) -> None:
        self._download_dir = download_dir

    def can_handle(self, request: str) -> bool:
        r = request.lower()
        return any(m in r for m in _URL_MARKERS)

    def acquire(self, request: str) -> AcquiredSource:  # pragma: no cover
        # Lazy import: yt-dlp is a heavy optional dependency.
        import yt_dlp

        owns_download_dir = self._download_dir is None
        out_dir = self._download_dir
        if out_dir is None:
            out_dir = Path(tempfile.mkdtemp(prefix="ricesearcher_"))
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            opts = {
                "outtmpl": str(out_dir / "%(id)s.%(ext)s"),
                # A transcription-first tool MUST get audio: YouTube serves video and
                # audio as separate DASH streams, so select the best of each and let
                # ffmpeg merge them. The bare "mp4/best" fallback can yield a
                # video-only stream (no audio → transcription fails).
                "format": "bestvideo*+bestaudio/best",
                "merge_output_format": "mp4",
                "quiet": True,
                "noplaylist": True,
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(request, download=True)
                # Prefer yt-dlp's final post-processed path. requested_downloads
                # can point at a video-only intermediate from a DASH merge.
                prepared = (
                    ydl.prepare_filename(info)
                    if hasattr(ydl, "prepare_filename")
                    else None
                )
                candidates = [info.get("filepath"), prepared]
                candidates.extend(
                    item.get("filepath")
                    for item in info.get("requested_downloads") or []
                    if isinstance(item, dict)
                )
                media_path = next(
                    (
                        Path(path)
                        for path in candidates
                        if path and Path(path).is_file()
                    ),
                    None,
                )
                if media_path is None:
                    raise FileNotFoundError(
                        "yt-dlp did not produce a usable media file"
                    )
            return AcquiredSource(
                kind=SourceKind.YOUTUBE,
                ref=request,
                media_path=media_path,
                title=info.get("title", ""),
                channel=info.get("uploader", ""),
                published_at=_normalize_upload_date(info.get("upload_date")),
                duration_s=float(info.get("duration") or 0.0),
                extra={"video_id": info.get("id", "")},
                owned_temp_dir=out_dir if owns_download_dir else None,
            )
        except Exception:
            if owns_download_dir:
                _remove_owned_download_dir(out_dir)
            raise


def _remove_owned_download_dir(path: Path) -> None:
    """Best-effort removal for a temporary directory owned by this adapter."""
    try:
        shutil.rmtree(path)
    except Exception:
        # Cleanup must never replace the acquisition error with a cleanup error.
        pass
