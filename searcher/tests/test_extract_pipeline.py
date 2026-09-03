"""The extract-and-score pipeline (FR-5)."""

from __future__ import annotations

from pathlib import Path

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.library.store import Library
from ricesearcher.models import Source, SourceKind, TranscriptWord
from ricesearcher.pipeline import DEFAULT_PAD_S, extract_and_score

_PROFILE = BeatProfile(version="pv1", name="b", brief="b", keywords=["love", "song"])


def _source(kind: SourceKind = SourceKind.YOUTUBE) -> Source:
    words = []
    t = 0.0
    for w in ["I", "love", "this", "song"] * 8:  # ~one keyword-rich window
        words.append(TranscriptWord(w, t, t + 0.5))
        t += 0.5
    return Source(
        id="srcX",
        kind=kind,
        ref="r",
        media_path="/m.mp4",
        duration_s=t,
        words=words,
    )


def test_extract_and_score_persists_scored_slices(tmp_path: Path, fake_scorer) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = _source()
        lib.upsert_source(src)
        slices = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib
        )
        assert slices
        persisted = lib.list_slices(source_id="srcX")
        assert len(persisted) == len(slices)
        top = persisted[0]
        # Provenance recorded.
        assert top.beat_profile_version == "pv1"
        assert top.scorer_model == "fake-model"
        assert top.rights_risk == "med"  # youtube
        # Padded window brackets the intended in/out (ADR Q4b).
        assert top.pad_in <= top.target_in
        assert top.pad_out >= top.target_out
        assert top.pad_in == max(0.0, top.target_in - DEFAULT_PAD_S)


def test_padding_clamps_to_source_bounds(tmp_path: Path, fake_scorer) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = _source()
        lib.upsert_source(src)
        slices = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib
        )
        for s in slices:
            assert s.pad_in >= 0.0
            assert s.pad_out <= src.duration_s


def test_rescore_replaces_in_place(tmp_path: Path, fake_scorer) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = _source()
        lib.upsert_source(src)
        first = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib
        )
        again = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib
        )
        # Deterministic ids → same count, no duplication.
        assert {s.id for s in first} == {s.id for s in again}
        assert len(lib.list_slices(source_id="srcX")) == len(first)


def test_local_source_is_low_rights_risk(tmp_path: Path, fake_scorer) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = _source(kind=SourceKind.LOCAL)
        lib.upsert_source(src)
        slices = extract_and_score(
            src, profile=_PROFILE, scorer=fake_scorer, library=lib
        )
        assert all(s.rights_risk == "low" for s in slices)


def test_empty_transcript_yields_no_slices(tmp_path: Path, fake_scorer) -> None:
    with Library(tmp_path / "lib.sqlite3") as lib:
        src = Source(id="empty", kind=SourceKind.LOCAL, ref="r", media_path="/m")
        lib.upsert_source(src)
        assert (
            extract_and_score(src, profile=_PROFILE, scorer=fake_scorer, library=lib)
            == []
        )
