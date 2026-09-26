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


def test_delete_removes_file_and_prunes_shard(tmp_path: Path, media_file: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    _digest, dest = cache.put(media_file)
    shard = dest.parent
    assert cache.delete(dest) is True
    assert not dest.exists()
    assert not shard.exists()  # empty shard dir is pruned
    # deleting an already-gone file is a no-op, not an error
    assert cache.delete(dest) is False


def test_delete_refuses_path_outside_root(tmp_path: Path) -> None:
    # A media_path that escaped the content-addressed tree must never be unlinked.
    cache = MediaCache(tmp_path / "cache")
    cache.root.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "precious.txt"
    outside.write_text("do not delete")
    assert cache.delete(outside) is False
    assert outside.is_file()


def test_clear_wipes_all_and_recreates_root(tmp_path: Path, media_file: Path) -> None:
    cache = MediaCache(tmp_path / "cache")
    cache.put(media_file)
    (cache.root / "loose.bin").write_bytes(b"x")  # a non-sharded stray file too
    removed = cache.clear()
    assert removed == 2
    assert cache.root.is_dir()
    assert not any(cache.root.iterdir())


def test_delete_survives_file_vanishing_after_check(
    tmp_path: Path, media_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Regression (F1): a concurrent delete can unlink the file between our
    # is_file() check and our own unlink(). That must return False, not raise.
    cache = MediaCache(tmp_path / "cache")
    _digest, dest = cache.put(media_file)

    def racing_unlink(self: Path, *a: object, **k: object) -> None:
        raise FileNotFoundError(self)

    monkeypatch.setattr(Path, "unlink", racing_unlink)
    assert cache.delete(dest) is False


def test_clear_handles_symlinked_child(tmp_path: Path, media_file: Path) -> None:
    # Regression (F3): a symlinked dir under root must not make clear() raise,
    # and its target must never be followed/deleted.
    cache = MediaCache(tmp_path / "cache")
    cache.put(media_file)
    external = tmp_path / "external_dir"
    external.mkdir()
    (external / "keep.bin").write_bytes(b"x")
    (cache.root / "linkshard").symlink_to(external, target_is_directory=True)

    cache.clear()  # must not raise on the symlinked child

    assert cache.root.is_dir() and not any(cache.root.iterdir())
    assert (external / "keep.bin").is_file()  # target preserved
