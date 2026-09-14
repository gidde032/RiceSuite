"""Partition regression (profiles-spec "First reliability risk").

A re-score, dedup pass, or handoff under one profile must never touch another
profile's rows. First half here: score one source under two profiles, re-score
the second, assert the first profile's rows are byte-identical. The handoff half
lands with P4.
"""

from __future__ import annotations

import json
from pathlib import Path

from ricesearcher.beat.profile import load_profile
from ricesearcher.library.store import Library
from ricesearcher.models import Source, SourceKind, TranscriptWord
from ricesearcher.pipeline import extract_and_score


def _write_profile(profiles_dir: Path, profile_id: str) -> None:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{profile_id}.json").write_text(
        json.dumps(
            {
                "version": f"{profile_id}-v1",
                "name": profile_id,
                "brief": "b",
                "keywords": ["love", "song"],
            }
        ),
        encoding="utf-8",
    )


def _source() -> Source:
    # 40 words, 0.5 s apart (~20 s, one utterance) so the prefilter yields a
    # window regardless of profile. "love" fires the keyword feature.
    words = [
        TranscriptWord("love" if i % 4 == 0 else "song", i * 0.5, i * 0.5 + 0.4)
        for i in range(40)
    ]
    return Source(
        id="src",
        kind=SourceKind.LOCAL,
        ref="r",
        media_path="/m.mp4",
        duration_s=20.0,
        words=words,
    )


def test_rescore_other_profile_leaves_first_untouched(tmp_path, fake_scorer) -> None:
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "alpha")
    _write_profile(profiles, "beta")
    prof_a = load_profile("alpha", profiles_dir=profiles)
    prof_b = load_profile("beta", profiles_dir=profiles)
    source = _source()

    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        extract_and_score(source, profile=prof_a, scorer=fake_scorer, library=lib)
        a_before = lib.list_slices(profile_id="alpha")
        assert a_before, "alpha slices must be stamped with its profile id"

        extract_and_score(source, profile=prof_b, scorer=fake_scorer, library=lib)
        a_after = lib.list_slices(profile_id="alpha")
        b_after = lib.list_slices(profile_id="beta")

        assert a_after == a_before, "re-scoring beta changed alpha's rows"
        assert b_after, "beta must be scored into its own partition"
        assert not ({s.id for s in a_after} & {s.id for s in b_after})
