"""Partition regression (profiles-spec "First reliability risk").

A re-score, dedup pass, or handoff under one profile must never touch another
profile's rows. Three guards:

- ``test_profile_id_is_stamped``: scoring stamps each row with its profile id.
- ``test_rescore_other_profile_leaves_first_untouched``: re-scoring one profile
  leaves the other profile's rows byte-identical. It snapshots rows by id and
  never filters by ``profile_id``, so the leak cannot hide behind that column.
- ``test_handoff_one_profile_leaves_other_selected``: handoff is scoped too.
"""

from __future__ import annotations

import json
from pathlib import Path

from ricesearcher.beat.profile import load_profile
from ricesearcher.config import Config
from ricesearcher.handoff.writer import hand_off_selected
from ricesearcher.library.store import Library
from ricesearcher.models import SliceStatus, Source, SourceKind, TranscriptWord
from ricesearcher.pipeline import extract_and_score


class _FakeExtractor:
    """Write a placeholder clip file; no ffmpeg needed."""

    def extract(self, source, start, end, dest) -> float:
        dest.write_bytes(b"clip")
        return end - start


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


def _snapshot_by_id(lib: Library) -> dict[str, tuple]:
    """Every candidate row as a full-field tuple, keyed by id.

    Reads the stored bytes directly and never filters by ``profile_id``, so a
    cross-profile overwrite cannot hide behind the very column under test.
    """
    rows = lib._conn.execute("SELECT * FROM candidate_slices").fetchall()
    return {row["id"]: tuple(row) for row in rows}


def test_profile_id_is_stamped(tmp_path, fake_scorer) -> None:
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "alpha")
    prof_a = load_profile("alpha", profiles_dir=profiles)
    source = _source()

    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        extract_and_score(source, profile=prof_a, scorer=fake_scorer, library=lib)
        rows = lib.list_slices(profile_id="alpha")
        assert rows, "alpha slices must be stamped with its profile id"
        assert all(s.profile_id == "alpha" for s in rows)


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
        # Only alpha's rows exist now. Snapshot them by id, not by profile_id:
        # the leak this guards would corrupt or delete rows the filter relies on.
        alpha_before = _snapshot_by_id(lib)
        assert alpha_before, "alpha must produce rows to guard"
        alpha_ids = set(alpha_before)

        extract_and_score(source, profile=prof_b, scorer=fake_scorer, library=lib)
        extract_and_score(source, profile=prof_b, scorer=fake_scorer, library=lib)

        after = _snapshot_by_id(lib)
        alpha_after = {rid: row for rid, row in after.items() if rid in alpha_ids}
        assert alpha_after == alpha_before, "scoring beta changed alpha's rows"
        assert len(alpha_after) == len(alpha_before), "alpha row count changed"


def test_handoff_one_profile_leaves_other_selected(tmp_path, fake_scorer) -> None:
    profiles = tmp_path / "profiles"
    _write_profile(profiles, "alpha")
    _write_profile(profiles, "beta")
    prof_a = load_profile("alpha", profiles_dir=profiles)
    prof_b = load_profile("beta", profiles_dir=profiles)

    media = tmp_path / "src.mp4"
    media.write_bytes(b"media-bytes")
    src = _source()
    src.media_path = str(media)

    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        lib.upsert_source(src)
        extract_and_score(src, profile=prof_a, scorer=fake_scorer, library=lib)
        extract_and_score(src, profile=prof_b, scorer=fake_scorer, library=lib)
        # Select one slice in each profile.
        a_slice = lib.list_slices(profile_id="alpha")[0]
        b_slice = lib.list_slices(profile_id="beta")[0]
        lib.update_slice_status(a_slice.id, SliceStatus.SELECTED)
        lib.update_slice_status(b_slice.id, SliceStatus.SELECTED)

        result = hand_off_selected(
            lib,
            extractor=_FakeExtractor(),
            duration_prober=lambda _path: 20.0,
            config=cfg,
            profile_id="alpha",
        )
        assert result["clip_count"] == 1  # only alpha's selected slice
        assert lib.get_slice(a_slice.id).status is SliceStatus.HANDED_OFF
        assert lib.get_slice(b_slice.id).status is SliceStatus.SELECTED  # untouched
