"""Align pasted lyric text to whisper-derived reference timings.

Pure standard-library module. No network, no ffmpeg, no torch. Produces
word-level ``Word`` objects with timing borrowed from anchored reference words
or evenly distributed across the vocal span.
"""

from __future__ import annotations

import re
import unicodedata

from app.models import LyricsResult
from app.models import Word as WordModel

ANCHOR_MIN = 0.25
MIN_WORD_S = 0.05
# When a matched anchor's delivered start moves further than this from its
# reference timing (to keep every word >= MIN_WORD_S and inside the clip), the
# result flags a "timing approximate" signal for the UI. Set to ADR-002's
# highlight-tracking tolerance. Diagnostic only; timing is unchanged (A1).
ANCHOR_DRIFT_WARN_S = 0.5

_STRIP_RE = re.compile(r"^[^\w']+|[^\w']+$", re.UNICODE)

# Apostrophe variants that must collapse to a single code point. Lyrics pasted
# from the web or typed with smart quotes use U+2019 etc.; whisper emits ASCII
# U+0027 for the same contractions. Canonicalizing lets them match (A2).
_APOSTROPHES = frozenset("'\u2018\u2019\u201b\u02bc\u2032")

# Hyphens / dashes to split compound tokens on for matching (A3).
_HYPHEN_SPLIT_RE = re.compile(r"[-\u2010-\u2015]")


def normalize(token: str) -> str:
    stripped = _STRIP_RE.sub("", token).lower()
    out: list[str] = []
    for i, c in enumerate(stripped):
        canonical = "'" if c in _APOSTROPHES else c
        if unicodedata.category(c).startswith("P"):
            if (
                canonical == "'"
                and 0 < i < len(stripped) - 1
                and stripped[i - 1].isalpha()
                and stripped[i + 1].isalpha()
            ):
                out.append("'")
        else:
            out.append(c)
    return "".join(out)


def _match_units(tokens: list[str]) -> list[tuple[int, str]]:
    """Normalized match units, each mapped to its display-token index.

    Hyphenated tokens split into sub-units so they can anchor against whisper's
    word-split transcription (e.g. "mother-in-law" -> "mother", "in", "law");
    the original token text is preserved for display (A3). Tokens without a
    hyphen yield a single unit, so non-hyphenated inputs are unchanged.
    """
    units: list[tuple[int, str]] = []
    for i, tok in enumerate(tokens):
        for part in _HYPHEN_SPLIT_RE.split(tok):
            n = normalize(part)
            if n:
                units.append((i, n))
    return units


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


def _lcs_matches(a: list[str], b: list[str]) -> list[tuple[int, int]]:
    """Match tokens by longest common subsequence, in chronological order.

    Maximizes the matched count and preserves order. On a tie it prefers the
    earliest reference occurrence: when the lyric token could match now or
    later without changing the matched count, it leaves the lyric token
    unanchored and keeps the reference cursor early. Iterative DP, no recursion.
    """
    n, m = len(a), len(b)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        row = dp[i]
        next_row = dp[i + 1]
        for j in range(m - 1, -1, -1):
            if a[i] == b[j]:
                row[j] = next_row[j + 1] + 1
            elif next_row[j] >= row[j + 1]:
                row[j] = next_row[j]
            else:
                row[j] = row[j + 1]

    matches: list[tuple[int, int]] = []
    i = j = 0
    while i < n and j < m:
        if a[i] == b[j] and dp[i][j] == dp[i + 1][j + 1] + 1:
            matches.append((i, j))
            i += 1
            j += 1
        elif dp[i + 1][j] >= dp[i][j + 1]:
            i += 1
        else:
            j += 1
    return matches


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

    ref_clean = [
        w for w in reference if w.end > w.start and w.start >= 0 and w.end <= duration
    ]

    norm_ref = [normalize(w.text) for w in ref_clean]

    matchable_lyric = _match_units(tokens)
    matchable_ref = [(i, n) for i, n in enumerate(norm_ref) if n]

    if ref_clean and ref_clean[-1].end > ref_clean[0].start:
        span_start = ref_clean[0].start
        span_end = ref_clean[-1].end
    else:
        span_start = 0.0
        span_end = duration

    ml_norms = [n for _, n in matchable_lyric]
    mr_norms = [n for _, n in matchable_ref]
    anchored: dict[int, tuple[float, float]] = {}
    for a_idx, b_idx in _lcs_matches(ml_norms, mr_norms):
        orig_lyric = matchable_lyric[a_idx][0]
        orig_ref = matchable_ref[b_idx][0]
        ref_s, ref_e = ref_clean[orig_ref].start, ref_clean[orig_ref].end
        if orig_lyric in anchored:
            # A hyphenated token matched multiple ref words: span them all.
            prev_s, prev_e = anchored[orig_lyric]
            anchored[orig_lyric] = (min(prev_s, ref_s), max(prev_e, ref_e))
        else:
            anchored[orig_lyric] = (ref_s, ref_e)

    matchable_tokens = {i for i, _ in matchable_lyric}
    denom = len(matchable_tokens) if matchable_tokens else len(tokens)
    anchor_rate = len(anchored) / denom

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
        timings = _clamp_to_duration(timings, duration)
        words = _build_words(tokens, timings, line_starts, duration)
        return LyricsResult(words=words, anchor_rate=anchor_rate, method="even_fill")

    starts_ends: list[tuple[float, float] | None] = [None] * len(tokens)
    for idx, (s, e) in anchored.items():
        starts_ends[idx] = (s, e)

    _interpolate_gaps(starts_ends, anchored, tokens, span_start, span_end)

    timings_raw = [(s, e) for s, e in starts_ends]  # type: ignore[misc]
    timings_final = _clamp_to_duration(timings_raw, duration)
    words = _build_words(tokens, timings_final, line_starts, duration)
    drift = _max_anchor_drift(words, anchored)
    return LyricsResult(
        words=words,
        anchor_rate=anchor_rate,
        method="anchors",
        anchor_drift=drift,
        anchor_drift_warning=drift > ANCHOR_DRIFT_WARN_S,
    )


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


def _max_anchor_drift(
    words: list[WordModel], anchored: dict[int, tuple[float, float]]
) -> float:
    """Largest gap (seconds) between a matched anchor's delivered and reference start.

    Anchors can be shifted to keep every word >= MIN_WORD_S and inside the clip
    (see A1). This measures how far the worst one moved so the UI can flag a
    rare, large shift; it does not change any timing.
    """
    drift = 0.0
    for idx, (ref_start, _ref_end) in anchored.items():
        drift = max(drift, abs(words[idx].start - ref_start))
    return drift


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
    if words and words[-1].end > duration > 0:
        # Infeasible case: more words than MIN_WORD_S slots in the clip.
        # Compress every timing uniformly so the block stays inside the clip.
        # Widths shrink below MIN_WORD_S; order and non-overlap survive.
        factor = duration / words[-1].end
        words = [
            WordModel(
                text=w.text,
                start=w.start * factor,
                end=w.end * factor,
                line_start=w.line_start,
            )
            for w in words
        ]
    return words
