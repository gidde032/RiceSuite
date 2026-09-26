"""The pull pipeline (SPEC §9 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import SourceKind
from ricesearcher.pipeline import NoAcquirerError, pull


def _wire(tmp_path: Path):
    return MediaCache(tmp_path / "cache"), Library(tmp_path / "lib.sqlite3")


class OwnedTempAcquirer:
    def __init__(self, root: Path) -> None:
        self.root = root

    def can_handle(self, request: str) -> bool:
        return request == "owned"

    def acquire(self, request: str) -> AcquiredSource:
        self.root.mkdir()
        media_path = self.root / "video.mp4"
        media_path.write_bytes(b"owned-media")
        return AcquiredSource(
            kind=SourceKind.YOUTUBE,
            ref=request,
            media_path=media_path,
            owned_temp_dir=self.root,
        )


def test_pull_happy_path(tmp_path, fake_acquirer, fake_transcriber) -> None:
    cache, lib = _wire(tmp_path)
    src = pull(
        "fake",
        acquirers=[fake_acquirer],
        transcriber=fake_transcriber,
        cache=cache,
        library=lib,
    )
    assert src.title == "Fake Source"
    assert src.transcript_text == "hello world"
    # Source id is the content hash; the row is retrievable.
    assert lib.get_source(src.id) is not None
    assert Path(src.media_path).is_file()
    lib.close()


def test_pull_no_acquirer_raises(tmp_path, fake_transcriber) -> None:
    cache, lib = _wire(tmp_path)
    with pytest.raises(NoAcquirerError):
        pull(
            "unhandled",
            acquirers=[WatchFolderAcquirer()],
            transcriber=fake_transcriber,
            cache=cache,
            library=lib,
        )
    lib.close()


def test_pull_same_bytes_dedupes_to_one_row(
    tmp_path, fake_acquirer, fake_transcriber
) -> None:
    cache, lib = _wire(tmp_path)
    a = pull(
        "fake",
        acquirers=[fake_acquirer],
        transcriber=fake_transcriber,
        cache=cache,
        library=lib,
    )
    b = pull(
        "fake",
        acquirers=[fake_acquirer],
        transcriber=fake_transcriber,
        cache=cache,
        library=lib,
    )
    assert a.id == b.id
    assert len(lib.list_sources()) == 1
    lib.close()


def test_pull_cleans_owned_temp_dir_after_cache_and_keeps_cached_media(
    tmp_path: Path, fake_transcriber
) -> None:
    cache, lib = _wire(tmp_path)
    acquirer = OwnedTempAcquirer(tmp_path / "owned-download")
    src = pull(
        "owned",
        acquirers=[acquirer],
        transcriber=fake_transcriber,
        cache=cache,
        library=lib,
    )

    assert not acquirer.root.exists()
    assert Path(src.media_path).is_file()
    lib.close()


def test_pull_cleans_owned_temp_dir_when_cache_fails(tmp_path: Path) -> None:
    _cache, lib = _wire(tmp_path)
    acquirer = OwnedTempAcquirer(tmp_path / "owned-download")

    class FailingCache:
        def put(self, _media_path: Path):
            raise RuntimeError("cache failed")

    with pytest.raises(RuntimeError, match="cache failed"):
        pull(
            "owned",
            acquirers=[acquirer],
            transcriber=object(),
            cache=FailingCache(),
            library=lib,
        )

    assert not acquirer.root.exists()
    lib.close()


def test_pull_cleans_owned_temp_dir_when_transcription_fails(tmp_path: Path) -> None:
    cache, lib = _wire(tmp_path)
    acquirer = OwnedTempAcquirer(tmp_path / "owned-download")

    class FailingTranscriber:
        def transcribe(self, _media_path: Path):
            raise RuntimeError("transcription failed")

    with pytest.raises(RuntimeError, match="transcription failed"):
        pull(
            "owned",
            acquirers=[acquirer],
            transcriber=FailingTranscriber(),
            cache=cache,
            library=lib,
        )

    assert not acquirer.root.exists()
    lib.close()


def test_pull_cleans_owned_temp_dir_when_persistence_fails(tmp_path: Path) -> None:
    cache, lib = _wire(tmp_path)
    acquirer = OwnedTempAcquirer(tmp_path / "owned-download")

    def fail_persist(_source):
        raise RuntimeError("persistence failed")

    lib.upsert_source = fail_persist
    with pytest.raises(RuntimeError, match="persistence failed"):
        pull(
            "owned",
            acquirers=[acquirer],
            transcriber=type(
                "Transcriber", (), {"transcribe": lambda _self, _path: []}
            )(),
            cache=cache,
            library=lib,
        )

    assert not acquirer.root.exists()
    lib.close()


def test_pull_preserves_primary_error_when_cleanup_fails(
    tmp_path: Path, monkeypatch
) -> None:
    cache, lib = _wire(tmp_path)
    acquirer = OwnedTempAcquirer(tmp_path / "owned-download")

    def failing_cleanup(_path):
        raise OSError("cleanup failed")

    monkeypatch.setattr("ricesearcher.pipeline.shutil.rmtree", failing_cleanup)

    class FailingTranscriber:
        def transcribe(self, _media_path: Path):
            raise RuntimeError("primary failure")

    with pytest.raises(RuntimeError, match="primary failure"):
        pull(
            "owned",
            acquirers=[acquirer],
            transcriber=FailingTranscriber(),
            cache=cache,
            library=lib,
        )

    lib.close()
