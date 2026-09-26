"""Media custody under concurrent pulls (RiceSuite #17, RiceSearcher #6).

Source delete, full cache clear, and failed-pull cleanup each check that no
source references a cached file and then unlink it. A same-content pull that
commits its row in between must never be left pointing at a missing file.
Each test plays one interleaving with the real cache, library, and web app in
temporary storage; the invariant is the same: every committed row keeps its
media file.
"""

from __future__ import annotations

import fcntl
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.acquire.base import AcquiredSource
from ricesearcher.config import Config
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import SourceKind, TranscriptWord
from ricesearcher.pipeline import pull


class FileAcquirer:
    def __init__(self, media: Path) -> None:
        self.media = media

    def can_handle(self, request: str) -> bool:
        return True

    def acquire(self, request: str) -> AcquiredSource:
        return AcquiredSource(kind=SourceKind.LOCAL, ref=request, media_path=self.media)


class CallbackTranscriber:
    """Runs ``during`` while it transcribes: the window a concurrent action
    has in real use, since transcription takes minutes."""

    def __init__(self, during=None, fail: bool = False) -> None:
        self.during = during
        self.fail = fail

    def transcribe(self, media_path: Path) -> list[TranscriptWord]:
        if self.during is not None:
            during, self.during = self.during, None
            during()
        if self.fail:
            raise RuntimeError("transcription failed")
        return [TranscriptWord("hello", 0.0, 0.4)]


@pytest.fixture
def storage(tmp_path: Path):
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    media = tmp_path / "incoming" / "clip.mp4"
    media.parent.mkdir()
    media.write_bytes(b"\x00same-content-bytes\x01")
    return cfg, media


def _client(cfg: Config) -> TestClient:
    from ricesearcher.web.app import create_app

    return TestClient(create_app(cfg))


def _pull(cfg: Config, media: Path, transcriber, library: Library | None = None):
    own = library is None
    lib = library or Library(cfg.db_path)
    try:
        return pull(
            "clip",
            acquirers=[FileAcquirer(media)],
            transcriber=transcriber,
            cache=MediaCache(cfg.cache_dir),
            library=lib,
        )
    finally:
        if own:
            lib.close()


def _assert_every_row_has_its_media(cfg: Config) -> None:
    with Library(cfg.db_path) as lib:
        sources = lib.list_sources()
    for source in sources:
        assert Path(source.media_path).is_file(), f"{source.id} lost its media"


def test_source_delete_during_a_same_content_pull(storage) -> None:
    cfg, media = storage
    first = _pull(cfg, media, CallbackTranscriber())
    client = _client(cfg)

    def delete_the_source():
        res = client.post(f"/api/sources/{first.id}/delete")
        assert res.json()["media_removed"] is True

    again = _pull(cfg, media, CallbackTranscriber(during=delete_the_source))
    assert again.id == first.id
    _assert_every_row_has_its_media(cfg)
    with Library(cfg.db_path) as lib:
        assert lib.get_source(first.id) is not None


def test_cache_clear_during_a_pull(storage) -> None:
    cfg, media = storage
    client = _client(cfg)

    def clear_everything():
        assert client.post("/api/cache/clear").status_code == 200

    source = _pull(cfg, media, CallbackTranscriber(during=clear_everything))
    _assert_every_row_has_its_media(cfg)
    assert Path(source.media_path).is_file()


def test_failed_pull_cleanup_under_a_concurrent_same_content_commit(
    storage, monkeypatch
) -> None:
    """Pull A created the cache file and fails. Its cleanup finds no reference;
    pull B of the same bytes (another process in real use) commits right then.
    Without custody, A's cleanup unlinks the file under B's new row."""
    cfg, media = storage
    real_check = Library.is_media_path_referenced
    other = {}

    def pull_b():
        other["source"] = _pull(cfg, media, CallbackTranscriber())

    def check_then_let_b_commit(self, path):
        referenced = real_check(self, path)
        if "thread" not in other:
            other["thread"] = threading.Thread(target=pull_b)
            other["thread"].start()
            # B commits now unless A's cleanup holds custody; then it waits.
            other["thread"].join(timeout=1.0)
        return referenced

    monkeypatch.setattr(Library, "is_media_path_referenced", check_then_let_b_commit)
    with pytest.raises(RuntimeError, match="transcription failed"):
        _pull(cfg, media, CallbackTranscriber(fail=True))
    other["thread"].join(timeout=10)
    assert "source" in other, "pull B did not finish"
    _assert_every_row_has_its_media(cfg)
    assert Path(other["source"].media_path).is_file()


def test_custody_excludes_another_process(storage) -> None:
    """The lock is an flock, so the CLI's pulls and the web app exclude each
    other: a second open file description cannot take it while it is held."""
    cfg, _ = storage
    cache = MediaCache(cfg.cache_dir)
    with cache.custody():
        with open(cache.custody_path, "a+") as other:
            with pytest.raises(BlockingIOError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(cache.custody_path, "a+") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)  # free again


def test_cache_clear_keeps_the_custody_lock_file(storage) -> None:
    cfg, _ = storage
    cache = MediaCache(cfg.cache_dir)
    with cache.custody():
        cache.clear()
    assert cache.custody_path.exists()


def test_one_cache_reached_two_ways_shares_one_custody_lock(tmp_path: Path) -> None:
    """Review S-3: the CLI and the web app may reach the same cache through a
    symlink or a relative path; they must still lock the same file."""
    real = tmp_path / "data" / "cache"
    real.mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "data")
    via_link = MediaCache(tmp_path / "link" / "cache")
    assert via_link.custody_path == MediaCache(real).custody_path
