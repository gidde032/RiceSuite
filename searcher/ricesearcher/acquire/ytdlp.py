"""YouTube acquirer via yt-dlp (D1).

``yt_dlp`` is imported lazily inside ``acquire`` so the core package and its
tests do not require it installed. This adapter only ever *downloads* source
media for local extraction — it never authenticates to or posts on any account.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.models import SourceKind

_URL_MARKERS = ("http://", "https://", "www.", "youtube.com", "youtu.be")


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

        out_dir = self._download_dir or Path(tempfile.mkdtemp(prefix="ricesearcher_"))
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
            # After a merge, the real output path is in requested_downloads;
            # prepare_filename can still report a pre-merge extension.
            downloads = info.get("requested_downloads") or []
            if downloads and downloads[0].get("filepath"):
                media_path = Path(downloads[0]["filepath"])
            else:
                media_path = Path(ydl.prepare_filename(info))
        return AcquiredSource(
            kind=SourceKind.YOUTUBE,
            ref=request,
            media_path=media_path,
            title=info.get("title", ""),
            channel=info.get("uploader", ""),
            published_at=str(info.get("upload_date", "")),
            duration_s=float(info.get("duration") or 0.0),
            extra={"video_id": info.get("id", "")},
        )
