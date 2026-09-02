"""Content-addressed media cache (SPEC §6, §8).

Source video and extracted clips are stored on disk keyed by a hash of their
bytes, so re-acquiring the same file de-duplicates for free and the SQLite row
only ever holds a reference. Bytes never live in SQLite.
"""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

_CHUNK = 1 << 20  # 1 MiB


def hash_file(path: Path) -> str:
    """Return the sha256 hex digest of a file's bytes, read in chunks."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_CHUNK):
            h.update(chunk)
    return h.hexdigest()


class MediaCache:
    """A content-addressed store of media files under ``root``."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def _dest_for(self, digest: str, suffix: str) -> Path:
        # Shard by the first two hex chars to keep directories small.
        return self.root / digest[:2] / f"{digest}{suffix}"

    def put(self, source: Path) -> tuple[str, Path]:
        """Copy ``source`` into the cache, returning its ``(digest, path)``.

        Idempotent: if the digest is already present the existing copy is kept
        and no second copy is written.
        """
        source = Path(source)
        if not source.is_file():
            raise FileNotFoundError(f"no such media file: {source}")
        digest = hash_file(source)
        dest = self._dest_for(digest, source.suffix.lower())
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Copy to a temp name then atomically rename so a reader never sees
            # a half-copied cache entry.
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            shutil.copy2(source, tmp)
            tmp.replace(dest)
        return digest, dest

    def path_for(self, digest: str, suffix: str) -> Path:
        """Return the cache path for a known digest + suffix (may not exist)."""
        return self._dest_for(digest, suffix.lower())
