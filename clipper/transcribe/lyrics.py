"""Align pasted lyric text to whisper-derived reference timings.

Pure standard-library module. No network, no ffmpeg, no torch. Produces
word-level ``Word`` objects with timing borrowed from anchored reference words
or evenly distributed across the vocal span.
"""

from __future__ import annotations

import difflib
import re

from app.models import LyricsResult
from app.models import Word as WordModel

ANCHOR_MIN = 0.25
MIN_WORD_S = 0.05

_STRIP_RE = re.compile(r"^[^\w']+|[^\w']+$", re.UNICODE)


def normalize(token: str) -> str:
    return _STRIP_RE.sub("", token).lower()


def _char_spread(
    tokens: list[str], start: float, end: float
) -> list[tuple[float, float]]:
    total_chars = sum(max(len(t), 1) for t in tokens)
    span = end - start
    result: list[tuple[float, float]] = []
    cursor = start
    for t in tokens:
        frac = max(len(t), 1) / total_chars
        w_end = cursor + frac * span
        result.append((cursor, w_end))
        cursor = w_end
    return result


def align(lyrics: str, reference: list[WordModel], duration: float) -> LyricsResult:
    lines = [ln for ln in lyrics.splitlines() if ln.strip()]
    if not lines:
        raise ValueError("empty lyric block")

    tokens: list[str] = []
    line_starts: list[int] = []
    for ln in lines:
        ws = ln.split()
        if not ws:
            continue
        line_starts.append(len(tokens))
        tokens.extend(ws)

    if not tokens:
        raise ValueError("empty lyric block")

    norm_lyrics = [normalize(t) for t in tokens]

    ref_clean = [
        w for w in reference if w.end > w.start and w.start >= 0 and w.end <= duration
    ]

    norm_ref = [normalize(w.text) for w in ref_clean]

    if ref_clean and ref_clean[-1].end > ref_clean[0].start:
        span_start = ref_clean[0].start
        span_end = ref_clean[-1].end
    else:
        span_start = 0.0
        span_end = duration

    matcher = difflib.SequenceMatcher(None, norm_lyrics, norm_ref, autojunk=False)
    anchored: dict[int, tuple[float, float]] = {}
    for match in matcher.get_matching_blocks():
        for k in range(match.size):
            lyric_idx = match.a + k
            ref_idx = match.b + k
            anchored[lyric_idx] = (ref_clean[ref_idx].start, ref_clean[ref_idx].end)

    anchor_rate = len(anchored) / len(tokens)

    if anchor_rate < ANCHOR_MIN:
        line_tokens: list[list[str]] = []
        for i, ls in enumerate(line_starts):
            nxt = line_starts[i + 1] if i + 1 < len(line_starts) else len(tokens)
            line_tokens.append(tokens[ls:nxt])
        line_chars = [sum(max(len(t), 1) for t in lt) for lt in line_tokens]
        total_chars = sum(line_chars)
        span = span_end - span_start
        timings: list[tuple[float, float]] = []
        cursor = span_start
        for li, lt in enumerate(line_tokens):
            line_span = (line_chars[li] / total_chars) * span
            word_timings = _char_spread(lt, cursor, cursor + line_span)
            timings.extend(word_timings)
            cursor += line_span
        words = _build_words(tokens, timings, line_starts, duration)
        return LyricsResult(words=words, anchor_rate=anchor_rate, method="even_fill")

    starts_ends: list[tuple[float, float] | None] = [None] * len(tokens)
    for idx, (s, e) in anchored.items():
        starts_ends[idx] = (s, e)

    _interpolate_gaps(starts_ends, anchored, tokens, span_start, span_end)

    timings_raw = [(s, e) for s, e in starts_ends]  # type: ignore[misc]
    timings_final = _clamp_to_duration(timings_raw, duration)
    words = _build_words(tokens, timings_final, line_starts, duration)
    return LyricsResult(words=words, anchor_rate=anchor_rate, method="anchors")


def _interpolate_gaps(
    starts_ends: list[tuple[float, float] | None],
    anchored: dict[int, tuple[float, float]],
    tokens: list[str],
    span_start: float,
    span_end: float,
) -> None:
    n = len(tokens)
    runs: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if i not in anchored:
            run_start = i
            while i < n and i not in anchored:
                i += 1
            runs.append((run_start, i))
        else:
            i += 1

    for run_start, run_end in runs:
        run_len = run_end - run_start
        prev_end = span_start
        if run_start > 0:
            prev_se = starts_ends[run_start - 1]
            if prev_se is not None:
                prev_end = prev_se[1]

        next_start = span_end
        if run_end < n:
            next_se = starts_ends[run_end]
            if next_se is not None:
                next_start = next_se[0]

        gap = next_start - prev_end
        needed = MIN_WORD_S * run_len
        if gap < needed:
            shortfall = needed - gap
            next_start += shortfall
            for j in range(run_end, n):
                if j in anchored:
                    old = starts_ends[j]
                    starts_ends[j] = (old[0] + shortfall, old[1] + shortfall)  # type: ignore[index]
            if run_end < n:
                next_start = starts_ends[run_end][0]  # type: ignore[index]

        run_tokens = tokens[run_start:run_end]
        spreads = _char_spread(run_tokens, prev_end, next_start)
        for k, (s, e) in enumerate(spreads):
            starts_ends[run_start + k] = (s, e)


def _clamp_to_duration(
    timings: list[tuple[float, float]], duration: float
) -> list[tuple[float, float]]:
    clamped = list(timings)
    for i in range(len(clamped) - 1, -1, -1):
        s, e = clamped[i]
        nxt_start = clamped[i + 1][0] if i + 1 < len(clamped) else duration
        end_i = min(e, nxt_start)
        start_i = min(s, end_i - MIN_WORD_S)
        clamped[i] = (max(start_i, 0.0), min(end_i, duration))
    return clamped


def _build_words(
    tokens: list[str],
    timings: list[tuple[float, float]],
    line_starts: list[int],
    duration: float,
) -> list[WordModel]:
    ls_set = set(line_starts)
    words: list[WordModel] = []
    for i, (tok, (s, e)) in enumerate(zip(tokens, timings, strict=True)):
        s = max(s, 0.0)
        e = min(e, duration)
        if e - s < MIN_WORD_S:
            e = s + MIN_WORD_S
        if i > 0 and s < words[-1].end:
            s = words[-1].end
        if e - s < MIN_WORD_S:
            e = s + MIN_WORD_S
        e = min(e, duration)
        if e - s < MIN_WORD_S:
            e = s + MIN_WORD_S
        words.append(WordModel(text=tok, start=s, end=e, line_start=(i in ls_set)))
    return words
