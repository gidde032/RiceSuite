"""Fail-before-fix regressions for the PR #11 review findings.

Covers C1-C8, C10, C11, C15, C17.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ricesearcher import cli
from ricesearcher.acquire.watchfolder import WatchFolderAcquirer
from ricesearcher.beat.profile import BeatProfile
from ricesearcher.extract.prefilter import _LAUGHTER, _score_window, prefilter
from ricesearcher.library.store import Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)
from ricesearcher.pipeline import extract_and_score
from ricesearcher.score.anthropic_scorer import (
    ScorerParseError,
    build_prompt,
    parse_response,
)
from tests.conftest import FakeTranscriber

_PROFILE = BeatProfile(version="pv", name="b", brief="b", keywords=["love", "ring"])


# -- C1: non-finite scores are rejected, not clamped to max -------------------


def test_c1_nan_and_inf_scores_become_zero() -> None:
    assert (
        parse_response('[{"index":0,"score":NaN,"rationale":"x"}]', 1)[0].score == 0.0
    )
    assert (
        parse_response('[{"index":0,"score":Infinity,"rationale":"x"}]', 1)[0].score
        == 0.0
    )


# -- C2: parse failure raises (not silent all-zero); trailing prose tolerated --


def test_c2_total_parse_failure_raises() -> None:
    with pytest.raises(ScorerParseError):
        parse_response("the model said no json at all", 2)


def test_c2_trailing_and_leading_prose_tolerated() -> None:
    got = parse_response('[{"index":0,"score":0.9,"rationale":"y"}] (range [0,1])', 1)
    assert got[0].score == 0.9
    got = parse_response('scores: [{"index":0,"score":0.8,"rationale":"z"}]', 1)
    assert got[0].score == 0.8


# -- C10: numeric-but-float indices apply; bools are rejected -----------------


def test_c10_float_index_applies_bool_rejected() -> None:
    assert (
        parse_response('[{"index":0.0,"score":0.7,"rationale":"z"}]', 1)[0].score == 0.7
    )
    # bool index must not land on index 1
    assert (
        parse_response('[{"index":true,"score":0.5,"rationale":"x"}]', 2)[1].score
        == 0.0
    )


# -- C3: padded window always brackets the intended out, even on duration drift --


def test_c3_pad_out_never_below_target_out(tmp_path: Path, fake_scorer) -> None:
    words = [TranscriptWord("love", i * 1.0, i * 1.0 + 1.0) for i in range(20)]
    src = Source(
        id="s",
        kind=SourceKind.LOCAL,
        ref="r",
        media_path="/m",
        duration_s=15.0,
        words=words,  # duration < last word end (20s): the drift case
    )
    with Library(tmp_path / "l.sqlite3") as lib:
        lib.upsert_source(src)
        slices = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib
        )
        assert slices
        for s in slices:
            assert s.pad_out >= s.target_out


# -- C4: an unknown status value doesn't poison the whole query ---------------


def test_c4_unknown_status_row_does_not_break_list(tmp_path: Path) -> None:
    db = tmp_path / "l.sqlite3"
    with Library(db) as lib:
        lib.upsert_source(
            Source(id="s", kind=SourceKind.LOCAL, ref="r", media_path="/m")
        )
    # Inject a row with a future/unknown status directly.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO candidate_slices "
        "(id, source_id, pad_in, pad_out, target_in, target_out, status) "
        "VALUES ('x','s',0,1,0,1,'archived')"
    )
    conn.commit()
    conn.close()
    with Library(db) as lib:
        slices = lib.list_slices()  # must not raise
        assert len(slices) == 1


# -- C5: keyword matching respects word boundaries ---------------------------


def test_c5_keyword_word_boundary() -> None:
    prof = BeatProfile(version="t", name="t", brief="t", keywords=["ring"])
    w = [
        TranscriptWord(t, i * 0.4, i * 0.4 + 0.4)
        for i, t in enumerate("during the boring morning".split())
    ]
    feats, _ = _score_window(w, prof)
    assert feats["keyword"] == 0.0
    w2 = [TranscriptWord("ring", 0.0, 0.4)]
    feats2, _ = _score_window(w2, prof)
    assert feats2["keyword"] > 0.0


# -- C6: merged windows never exceed MAX_S (gaps counted) ---------------------


def test_c6_merge_respects_max_span() -> None:
    words = [TranscriptWord("a", 0.0, 10.0)]  # utt A: 0-10
    words += [TranscriptWord("b", 11.3, 45.3)]  # utt B after a 1.3s gap: 11.3-45.3
    windows = prefilter(
        words, _PROFILE, source_id="s", max_s=45.0, min_s=12.0, gap_s=1.2
    )
    assert windows
    assert all(w.end - w.start <= 45.0 + 1e-6 for w in windows)


# -- C7: re-scoring clears stale candidate slices, preserves reviewed/selected --


def test_c7_rescore_clears_candidate_orphans(tmp_path: Path, fake_scorer) -> None:
    words = []
    t = 0.0
    for _ in range(6):
        words += [TranscriptWord("love", t, t + 0.4)]
        t += 0.4
        words += [TranscriptWord("song", t, t + 0.4)]
        t = words[-1].end + 5.0  # gaps -> multiple windows
    src = Source(id="s", kind=SourceKind.LOCAL, ref="r", media_path="/m", words=words)
    with Library(tmp_path / "l.sqlite3") as lib:
        lib.upsert_source(src)
        first = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib, top_k=3
        )
        assert len(first) >= 2
        # Mark a LOWER window selected (not the top one the re-score regenerates);
        # it must survive while stale candidate orphans are cleared.
        keep = first[-1]
        keep.status = SliceStatus.SELECTED
        lib.upsert_slices([keep])
        extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib, top_k=1
        )
        remaining = lib.list_slices(source_id="s")
        candidates = [s for s in remaining if s.status is SliceStatus.CANDIDATE]
        selected = [s for s in remaining if s.status is SliceStatus.SELECTED]
        assert len(candidates) == 1  # orphans cleared
        assert len(selected) == 1  # human decision preserved


# -- C8: ambiguous prefix reports one clean error, not two -------------------


def test_c8_ambiguous_prefix_single_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    from ricesearcher.config import load_config

    with Library(load_config().db_path) as lib:
        lib.upsert_source(
            Source(id="bbbb1111", kind=SourceKind.LOCAL, ref="r", media_path="/m")
        )
        lib.upsert_source(
            Source(id="bbbb2222", kind=SourceKind.LOCAL, ref="r", media_path="/m")
        )
    rc = cli.main(["slices", "--source", "bbbb"])
    err = capsys.readouterr().err
    assert rc == 2
    assert "ambiguous" in err
    assert "no source" not in err


# -- C11: no duplicate laughter token -----------------------------------------


def test_c11_no_duplicate_laughter_token() -> None:
    assert len(_LAUGHTER) == len(set(_LAUGHTER))


# -- C15: prompt fences candidate text as data --------------------------------


def test_c15_prompt_marks_candidates_as_data() -> None:
    from ricesearcher.models import CandidateWindow

    windows = [CandidateWindow(source_id="s", start=0, end=8, text="hello")]
    prompt = build_prompt(windows, _PROFILE)
    assert "data" in prompt.lower()  # an explicit data/never-instructions guard


# -- C17: slices listing can show the source title ----------------------------


def test_c17_slices_shows_source_title(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RICESEARCHER_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(cli, "_default_acquirers", lambda: [WatchFolderAcquirer()])
    monkeypatch.setattr(cli, "WhisperTranscriber", lambda **_: FakeTranscriber())
    from ricesearcher.config import load_config

    media = tmp_path / "My Great Interview.mp4"
    media.write_bytes(b"bytes")
    assert cli.main(["pull", str(media)]) == 0
    with Library(load_config().db_path) as lib:
        sid = lib.list_sources()[0].id
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="sl1",
                    source_id=sid,
                    pad_in=0,
                    pad_out=1,
                    target_in=0,
                    target_out=1,
                    transcript_span="x",
                    score=0.5,
                )
            ]
        )
    capsys.readouterr()
    assert cli.main(["slices"]) == 0
    assert "My Great Interview" in capsys.readouterr().out
