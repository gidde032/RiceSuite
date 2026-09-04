"""Acquirer protocol and the normalized result it yields."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from ricesearcher.models import SourceKind


@dataclass
class AcquiredSource:
    """A fetched media file plus provenance, before it enters the cache/library."""

    kind: SourceKind
    ref: str
    media_path: Path
    title: str = ""
    channel: str = ""
    published_at: str = ""
    duration_s: float = 0.0
    extra: dict = field(default_factory=dict)
    # When set, this directory was created by the acquirer for this pull and
    # the pipeline may remove it once the media has entered the cache. Paths
    # supplied by callers and local-file sources leave this unset.
    owned_temp_dir: Path | None = None


class Acquirer(Protocol):
    """Something that can turn a request string into an ``AcquiredSource``."""

    def can_handle(self, request: str) -> bool:
        """True if this acquirer owns ``request`` (a URL, query, or path)."""
        ...

    def acquire(self, request: str) -> AcquiredSource:
        """Fetch the material for ``request`` into a local file."""
        ...
