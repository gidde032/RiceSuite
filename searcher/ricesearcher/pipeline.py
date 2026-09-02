"""The pull pipeline (SPEC §9 Phase 1, FR-1/FR-2).

``pull`` routes a request to the first acquirer that can handle it, caches the
media content-addressed, transcribes it, and persists the source + transcript to
the library. Acquirers and the transcriber are injected so the pipeline is fully
testable without yt-dlp or faster-whisper.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from ricesearcher.acquire.base import Acquirer
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import Source
from ricesearcher.transcribe.base import Transcriber


class NoAcquirerError(RuntimeError):
    """No registered acquirer can handle the request."""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def pull(
    request: str,
    *,
    acquirers: Sequence[Acquirer],
    transcriber: Transcriber,
    cache: MediaCache,
    library: Library,
) -> Source:
    """Acquire, cache, transcribe, and store one source; return it.

    The first acquirer whose ``can_handle`` returns True wins. Media is keyed by
    content hash so re-pulling the same bytes de-duplicates the cache copy; the
    source id IS that hash, so re-pulling refreshes the same library row.
    """
    acquirer = next((a for a in acquirers if a.can_handle(request)), None)
    if acquirer is None:
        raise NoAcquirerError(f"no acquirer can handle: {request!r}")

    acquired = acquirer.acquire(request)
    digest, cached_path = cache.put(acquired.media_path)
    words = transcriber.transcribe(cached_path)

    source = Source(
        id=digest,
        kind=acquired.kind,
        ref=acquired.ref,
        media_path=str(cached_path),
        title=acquired.title,
        channel=acquired.channel,
        published_at=acquired.published_at,
        acquired_at=_now_iso(),
        duration_s=acquired.duration_s,
        words=list(words),
    )
    library.upsert_source(source)
    return source
