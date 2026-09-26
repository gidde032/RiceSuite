"""Heuristic prefilter (FR-3)."""

from __future__ import annotations

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.extract.prefilter import prefilter
from ricesearcher.models import TranscriptWord

_PROFILE = BeatProfile(
    version="t", name="t", brief="t", keywords=["love", "song", "married"]
)


def _words(spec: list[tuple[str, float, float]]) -> list[TranscriptWord]:
    return [TranscriptWord(t, s, e) for t, s, e in spec]


def _fill(words: list[str], start: float, per: float = 0.4) -> list[TranscriptWord]:
    out = []
    t = start
    for w in words:
        out.append(TranscriptWord(w, t, t + per))
        t += per
    return out


def test_empty_transcript_yields_no_candidates() -> None:
    assert prefilter([], _PROFILE, source_id="s") == []


def test_keyword_rich_window_outranks_filler() -> None:
    # Utterance A (keyword-rich) then a >gap silence then filler utterance B.
    a = _fill(["I", "love", "this", "song", "married"] * 4, start=0.0)  # ~8s
    b_start = a[-1].end + 5.0  # a silence gap splits utterances
    b = _fill(["um", "so", "anyway", "the", "schedule"] * 4, start=b_start)
    windows = prefilter(a + b, _PROFILE, source_id="s", min_s=3.0)
    assert windows
    # The keyword-rich window scores highest.
    assert "love" in windows[0].text
    assert windows[0].heuristic_score >= windows[-1].heuristic_score


def test_top_k_caps_results() -> None:
    words: list[TranscriptWord] = []
    t = 0.0
    for _ in range(20):
        words += _fill(["love", "song"] * 6, start=t)  # ~4.8s each
        t = words[-1].end + 5.0  # gap splits into separate utterances
    windows = prefilter(words, _PROFILE, source_id="s", top_k=3, min_s=3.0)
    assert len(windows) == 3


def test_long_utterance_is_split_under_max() -> None:
    # One continuous 120s utterance (no gaps) must be split into <=max_s windows.
    long = _fill(["word"] * 300, start=0.0)  # 300 * 0.4 = 120s
    windows = prefilter(long, _PROFILE, source_id="s", max_s=45.0, top_k=99)
    assert windows
    assert all(w.end - w.start <= 45.0 + 1e-6 for w in windows)
