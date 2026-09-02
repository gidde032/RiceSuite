"""Acquirer routing (D1)."""

from __future__ import annotations

from pathlib import Path

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
