"""Content-addressed cache (SPEC §6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ricesearcher.library.cache import MediaCache, hash_file


def test_hash_is_stable(media_file: Path) -> None:
    assert hash_file(media_file) == hash_file(media_file)


def test_put_stores_and_returns_digest(tmp_path: Path, media_file: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    digest, dest = cache.put(media_file)
    assert digest == hash_file(media_file)
    assert dest.is_file()
    assert dest.read_bytes() == media_file.read_bytes()
    # Sharded by first two hex chars.
    assert dest.parent.name == digest[:2]


def test_put_is_idempotent(tmp_path: Path, media_file: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    d1, p1 = cache.put(media_file)
    d2, p2 = cache.put(media_file)
    assert (d1, p1) == (d2, p2)
    # Only one file under the digest shard, no leftover .tmp.
    assert sorted(x.name for x in p1.parent.iterdir()) == [p1.name]


def test_put_missing_file_raises(tmp_path: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    with pytest.raises(FileNotFoundError):
        cache.put(tmp_path / "nope.mp4")
