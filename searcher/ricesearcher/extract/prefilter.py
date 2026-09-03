"""Heuristic candidate-window prefilter (FR-3).

Pure, side-effect-free, and dependency-light so it is unit-testable without the
web stack or an LLM. It segments a word-level transcript into utterances, merges
them into clip-length windows, scores each window on cheap signals drawn from the
beat profile, and returns the top-K shortlist. The LLM (Phase 2 scorer) only ever
sees this shortlist, which bounds scoring cost (SPEC §5).
"""

from __future__ import annotations

from collections.abc import Sequence

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.models import CandidateWindow, TranscriptWord

# Clip-length bounds and segmentation, in seconds.
MIN_S = 12.0
MAX_S = 45.0
GAP_S = 1.2  # a silence longer than this ends an utterance
DEFAULT_TOP_K = 12

_LAUGHTER = ("haha", "haha", "[laugh", "(laugh", "lol", "hehe")


def _segment_utterances(
    words: Sequence[TranscriptWord], gap_s: float
) -> list[list[TranscriptWord]]:
    """Split words into utterances on silences longer than ``gap_s``."""
    utterances: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []
    prev_end: float | None = None
    for w in words:
        if prev_end is not None and w.start - prev_end > gap_s:
            if current:
                utterances.append(current)
            current = []
        current.append(w)
        prev_end = w.end
    if current:
        utterances.append(current)
    return utterances


def _merge_windows(
    utterances: list[list[TranscriptWord]], min_s: float, max_s: float
) -> list[list[TranscriptWord]]:
    """Greedily merge utterances into windows between ``min_s`` and ``max_s``."""
    windows: list[list[TranscriptWord]] = []
    current: list[TranscriptWord] = []

    def dur(ws: list[TranscriptWord]) -> float:
        return ws[-1].end - ws[0].start if ws else 0.0

    for utt in utterances:
        # A single utterance longer than max_s is split into max_s-ish chunks.
        if dur(utt) > max_s:
            if current:
                windows.append(current)
                current = []
            windows.extend(_split_long(utt, max_s))
            continue
        if current and dur(current) + dur(utt) > max_s:
            windows.append(current)
            current = utt.copy()
        else:
            current.extend(utt)
            if dur(current) >= min_s:
                windows.append(current)
                current = []
    if current:
        windows.append(current)
    return windows


def _split_long(utt: list[TranscriptWord], max_s: float) -> list[list[TranscriptWord]]:
    chunks: list[list[TranscriptWord]] = []
    chunk: list[TranscriptWord] = []
    for w in utt:
        if chunk and w.end - chunk[0].start > max_s:
            chunks.append(chunk)
            chunk = []
        chunk.append(w)
    if chunk:
        chunks.append(chunk)
    return chunks


def _score_window(
    window: list[TranscriptWord], profile: BeatProfile
) -> tuple[dict[str, float], float]:
    text = " ".join(w.text for w in window)
    lowered = text.lower()
    duration = window[-1].end - window[0].start

    distinct_hits = sum(1 for kw in set(profile.keywords) if kw in lowered)
    keyword = min(distinct_hits / 3.0, 1.0)
    question = 1.0 if "?" in text else 0.0
    exclaim = 1.0 if "!" in text else 0.0
    laughter = 1.0 if any(tok in lowered for tok in _LAUGHTER) else 0.0
    duration_fit = 1.0 if MIN_S <= duration <= MAX_S else 0.4

    features = {
        "keyword": keyword,
        "question": question,
        "exclaim": exclaim,
        "laughter": laughter,
        "duration_fit": duration_fit,
    }
    score = (
        0.50 * keyword
        + 0.20 * duration_fit
        + 0.15 * question
        + 0.10 * laughter
        + 0.05 * exclaim
    )
    return features, round(score, 4)


def prefilter(
    words: Sequence[TranscriptWord],
    profile: BeatProfile,
    *,
    source_id: str,
    top_k: int = DEFAULT_TOP_K,
    min_s: float = MIN_S,
    max_s: float = MAX_S,
    gap_s: float = GAP_S,
) -> list[CandidateWindow]:
    """Return up to ``top_k`` candidate windows, highest heuristic score first."""
    if not words:
        return []
    utterances = _segment_utterances(words, gap_s)
    merged = _merge_windows(utterances, min_s, max_s)

    candidates: list[CandidateWindow] = []
    for window in merged:
        if not window:
            continue
        features, score = _score_window(window, profile)
        candidates.append(
            CandidateWindow(
                source_id=source_id,
                start=window[0].start,
                end=window[-1].end,
                text=" ".join(w.text for w in window),
                features=features,
                heuristic_score=score,
            )
        )
    # Highest score first; stable tie-break on start time for determinism.
    candidates.sort(key=lambda c: (-c.heuristic_score, c.start))
    return candidates[:top_k]
