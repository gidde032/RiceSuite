"""Acquirer routing (D1)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.acquire.ytdlp import YtDlpAcquirer
from ricesearcher.models import SourceKind


def test_watchfolder_handles_local_media(media_file: Path) -> None:
    acq = WatchFolderAcquirer()
    assert acq.can_handle(str(media_file)) is True
    got = acq.acquire(str(media_file))
    assert got.kind is SourceKind.LOCAL
    assert got.media_path == media_file.resolve()


def test_watchfolder_rejects_url_and_nonmedia(tmp_path: Path) -> None:
    acq = WatchFolderAcquirer()
    assert acq.can_handle("https://youtu.be/x") is False
    txt = tmp_path / "notes.txt"
    txt.write_text("hi")
    assert acq.can_handle(str(txt)) is False


def test_watchfolder_acquire_missing_raises() -> None:
    with pytest.raises(FileNotFoundError):
        WatchFolderAcquirer().acquire("/no/such/file.mp4")


def test_ytdlp_can_handle_urls_only() -> None:
    acq = YtDlpAcquirer()
    assert acq.can_handle("https://www.youtube.com/watch?v=x") is True
    assert acq.can_handle("youtu.be/abc") is True
    assert acq.can_handle("/local/path.mp4") is False


def test_ytdlp_default_download_dir_is_marked_owned(
    tmp_path: Path, monkeypatch
) -> None:
    owned_dir = tmp_path / "owned"
    monkeypatch.setattr(
        "ricesearcher.acquire.ytdlp.tempfile.mkdtemp",
        lambda prefix: str(owned_dir),
    )

    class FakeYoutubeDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _request, *, download):
            assert download is True
            media_path = Path(
                self._opts["outtmpl"].replace("%(id)s.%(ext)s", "video.mp4")
            )
            media_path.write_bytes(b"downloaded")
            return {
                "id": "video",
                "title": "Video",
                "requested_downloads": [{"filepath": str(media_path)}],
            }

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    got = YtDlpAcquirer().acquire("https://youtu.be/video")

    assert got.owned_temp_dir == owned_dir
    assert got.media_path.is_file()


def test_ytdlp_default_download_dir_is_cleaned_on_acquire_failure(
    tmp_path: Path, monkeypatch
) -> None:
    owned_dir = tmp_path / "owned"
    monkeypatch.setattr(
        "ricesearcher.acquire.ytdlp.tempfile.mkdtemp",
        lambda prefix: str(owned_dir),
    )

    class FailingYoutubeDL:
        def __init__(self, _opts):
            owned_dir.joinpath("partial.mp4").write_bytes(b"partial")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _request, *, download):
            raise RuntimeError("download failed")

    monkeypatch.setitem(
        sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FailingYoutubeDL)
    )
    with pytest.raises(RuntimeError, match="download failed"):
        YtDlpAcquirer().acquire("https://youtu.be/video")

    assert not owned_dir.exists()


def test_ytdlp_explicit_download_dir_is_not_marked_owned(
    tmp_path: Path, monkeypatch
) -> None:
    supplied_dir = tmp_path / "supplied"

    class FakeYoutubeDL:
        def __init__(self, opts):
            self._opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def extract_info(self, _request, *, download):
            media_path = Path(
                self._opts["outtmpl"].replace("%(id)s.%(ext)s", "video.mp4")
            )
            media_path.write_bytes(b"downloaded")
            return {"requested_downloads": [{"filepath": str(media_path)}]}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=FakeYoutubeDL))
    got = YtDlpAcquirer(download_dir=supplied_dir).acquire("https://youtu.be/video")

    assert got.owned_temp_dir is None
    assert supplied_dir.is_dir()
    assert got.media_path.is_file()
