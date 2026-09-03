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
from ricesearcher.beat.profile import BeatProfile
from ricesearcher.dedup.annotate import (
    OVERLAP_THRESHOLD,
    SIM_THRESHOLD,
    annotate_duplicates,
)
from ricesearcher.dedup.base import Embedder
from ricesearcher.extract.prefilter import DEFAULT_TOP_K, prefilter
from ricesearcher.library.cache import MediaCache
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.score.base import Scorer
from ricesearcher.transcribe.base import Transcriber

DEFAULT_PAD_S = 2.0
_RIGHTS_BY_KIND = {SourceKind.YOUTUBE: "med", SourceKind.LOCAL: "low"}


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


def _slice_id(source_id: str, target_in: float, target_out: float) -> str:
    """Deterministic id from the intended window, so re-scoring replaces in place."""
    return f"{source_id}:{int(round(target_in * 1000))}-{int(round(target_out * 1000))}"


def extract_and_score(
    source: Source,
    *,
    profile: BeatProfile,
    scorer: Scorer,
    library: Library,
    top_k: int = DEFAULT_TOP_K,
    pad_s: float = DEFAULT_PAD_S,
) -> list[CandidateSlice]:
    """Prefilter → score → persist candidate slices for one source (FR-3/4/5).

    Each slice carries a padded window around the intended in/out (ADR Q4b); the
    intended cut is metadata, tightened at review. Slice ids are deterministic, so
    re-scoring a source updates its slices in place rather than duplicating them.
    """
    windows = prefilter(source.words, profile, source_id=source.id, top_k=top_k)
    results = scorer.score(windows, profile)
    created = _now_iso()
    rights = _RIGHTS_BY_KIND.get(source.kind, "med")
    # Bound padding by the later of the reported duration and the last word's
    # timestamp: a container/ASR mismatch must never clamp pad_out below the
    # intended out (ADR Q4b: the padded window brackets the intended cut).
    last_word_end = source.words[-1].end if source.words else 0.0
    duration = max(source.duration_s, last_word_end)

    slices: list[CandidateSlice] = []
    for window, result in zip(windows, results, strict=True):
        target_in, target_out = window.start, window.end
        pad_out = target_out + pad_s
        if duration:
            pad_out = min(pad_out, duration)
        slices.append(
            CandidateSlice(
                id=_slice_id(source.id, target_in, target_out),
                source_id=source.id,
                pad_in=max(0.0, target_in - pad_s),
                pad_out=pad_out,
                target_in=target_in,
                target_out=target_out,
                transcript_span=window.text,
                score=result.score,
                rationale=result.rationale,
                heuristic_score=window.heuristic_score,
                heuristic_features=window.features,
                beat_profile_version=profile.version,
                scorer_model=scorer.model_name,
                rights_risk=rights,
                status=SliceStatus.CANDIDATE,
                created_at=created,
            )
        )

    # Re-scoring regenerates the candidate shortlist, so clear the source's prior
    # candidate slices first (a smaller top_k or an edited profile would otherwise
    # leave stale orphans). Human-touched slices (reviewed/selected/...) are never
    # deleted, and we don't overwrite one back to candidate.
    protected = {
        s.id
        for s in library.list_slices(source_id=source.id)
        if s.status is not SliceStatus.CANDIDATE
    }
    library.delete_candidate_slices(source.id)
    fresh = [s for s in slices if s.id not in protected]
    library.upsert_slices(fresh)
    return fresh


def annotate_library_duplicates(
    library: Library,
    embedder: Embedder,
    *,
    sim_threshold: float = SIM_THRESHOLD,
    overlap_threshold: float = OVERLAP_THRESHOLD,
) -> list[CandidateSlice]:
    """Recompute advisory duplicate annotations across the whole library (FR-6).

    Embeds every slice's transcript span, annotates duplicates (intra-source by
    time overlap, cross-source by embedding similarity), and persists the updated
    ``dup_*`` fields. This is a SIGNAL only — no slice is removed, hidden, or
    reordered; re-running it recomputes from scratch (idempotent).
    """
    slices = library.list_slices()
    if not slices:
        return []
    vectors = embedder.embed([s.transcript_span for s in slices])
    embeddings = {s.id: v for s, v in zip(slices, vectors, strict=True)}
    annotated = annotate_duplicates(
        slices,
        embeddings,
        sim_threshold=sim_threshold,
        overlap_threshold=overlap_threshold,
    )
    library.upsert_slices(annotated)
    return annotated
