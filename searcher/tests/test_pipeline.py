"""The pull pipeline (SPEC §9 Phase 1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.pipeline import NoAcquirerError, pull


def _wire(tmp_path: Path):
    return MediaCache(tmp_path / "cache"), Library(tmp_path / "lib.sqlite3")


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
