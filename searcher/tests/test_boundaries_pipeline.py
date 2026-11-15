"""Online boundaries through scoring, persistence and migration (RiceSuite #23)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.extract.boundaries import BoundaryReport
from ricesearcher.library.store import MIGRATIONS, Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.pipeline import SCORE_BATCH, extract_and_score
from ricesearcher.score.anthropic_scorer import MissingCredentialsError
from ricesearcher.score.base import ScoredResult
from tests.boundary_fakes import ScriptedClient, Sequenced, moment, refined, words

PROFILE = BeatProfile(version="v1", name="beat", brief="b", id="p")


def _source(n: int = 400) -> Source:
    ws = words(n)
    return Source(
        id="src",
        kind=SourceKind.YOUTUBE,
        ref="r",
        media_path="/m.mp4",
        duration_s=ws[-1].end,
        words=ws,
    )


class RecordingScorer:
    model_name = "fake-scorer"

    def __init__(self, score: float = 0.5) -> None:
        self.batches: list[list[str]] = []
        self.value = score

    def score(self, windows, profile):
        self.batches.append([w.text for w in windows])
        return [ScoredResult(self.value, f"r{i}") for i, _ in enumerate(windows)]


class BoomScorer:
    model_name = "boom"

    def score(self, windows, profile):
        raise RuntimeError("scoring down")


def _score(lib, source, client, scorer=None, profile=PROFILE):
    report = BoundaryReport()
    scorer = scorer or RecordingScorer()
    slices = extract_and_score(
        source,
        profile=profile,
        scorer=scorer,
        library=lib,
        model_client=client,
        report=report,
    )
    return slices, scorer, report


def _two(source):
    ws = source.words
    return json.dumps(
        [moment(ws[0].start, ws[39].end), moment(ws[100].start, ws[139].end)]
    )


def test_online_scoring_scores_the_exact_refined_text(tmp_path: Path) -> None:
    source = _source()
    ws = source.words
    client = ScriptedClient(
        discovery=lambda _p: json.dumps([moment(ws[10].start, ws[29].end)]),
        refinement=lambda _p: refined(6, 35),
    )
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        [s], scorer, _ = _score(lib, source, client)
    exact = " ".join(w.text for w in ws[6:36])
    assert scorer.batches == [[exact]]  # never the discovered 10..29 text
    assert s.transcript_span == exact
    assert (s.target_in, s.target_out) == (pytest.approx(2.95), pytest.approx(17.95))
    assert s.id == "src:p:2950-17950"
    assert s.score == 0.5 and s.scorer_model == "fake-scorer"
    assert s.extraction["methods"] == ["model-full"]

    with Library(tmp_path / "lib.sqlite3") as lib:  # provenance survives reload
        [stored] = lib.list_slices(profile_id="p")
    assert stored.extraction == s.extraction
    assert stored.extraction["proposals"][0]["refinement"]["status"] == "refined"


def test_coalesced_duplicates_are_scored_once_with_both_proposals(tmp_path) -> None:
    source = _source()
    client = ScriptedClient(
        discovery=lambda _p: _two(source), refinement=lambda _p: refined(20, 119)
    )
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        slices, scorer, report = _score(lib, source, client)
    assert len(slices) == 1 and len(scorer.batches[0]) == 1
    assert len(slices[0].extraction["proposals"]) == 2
    assert (report.proposals, report.candidates) == (2, 1)


def test_every_candidate_is_scored_in_bounded_batches_without_a_cutoff(
    tmp_path, monkeypatch
) -> None:
    from ricesearcher.extract import boundaries

    monkeypatch.setattr(boundaries, "BASE_CAPACITY", 30)
    source = _source(1200)
    ws = source.words
    many = json.dumps(
        [moment(ws[i].start, ws[i + 7].end) for i in range(0, 30 * 40, 40)]
    )
    client = ScriptedClient(discovery=lambda _p: many)
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        slices, scorer, _ = _score(lib, source, client, RecordingScorer(0.0))
        stored = lib.list_slices(profile_id="p")
    assert [len(b) for b in scorer.batches] == [SCORE_BATCH, SCORE_BATCH, 6]
    assert len(slices) == len(stored) == 30  # a zero score is kept, slots unfilled


def test_unresolved_refinement_is_persisted_with_the_review_flag(tmp_path) -> None:
    source = _source()
    client = ScriptedClient(
        discovery=lambda _p: _two(source),
        refinement=Sequenced(
            refined(0, 45, needs_more=True), "bad", "bad", then=lambda _p: "x"
        ),
    )
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        _score(lib, source, client)
        stored = lib.list_slices(profile_id="p")
    flags = sorted(s.extraction["refinement"][0] for s in stored)
    assert flags == ["needs_more_context", "refinement_failed"]
    assert all(s.extraction["needs_review"] for s in stored)
    ws = source.words
    starts = sorted(s.target_in for s in stored)
    assert starts == [0.0, pytest.approx(ws[100].start - 0.05)]  # originals kept


def test_human_touched_rows_survive_an_online_rescore(tmp_path) -> None:
    source = _source()
    client = ScriptedClient(discovery=lambda _p: _two(source))
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        first, _, _ = _score(lib, source, client)
        selected, edited = sorted(first, key=lambda s: s.target_in)
        lib.update_slice_status(selected.id, SliceStatus.SELECTED)
        lib.update_slice_window(edited.id, 49.0, 71.0)
        manual = replace(
            selected,
            id="manual:1",
            origin="manual",
            score=None,
            heuristic_score=None,
            status=SliceStatus.REVIEWED,
            extraction={},
        )
        lib.upsert_slices([manual])

        fresh, _, _ = _score(lib, source, client, RecordingScorer(0.9))
        rows = {s.id: s for s in lib.list_slices(profile_id="p")}

    assert fresh == []  # both final ids are protected human-touched rows
    assert rows[selected.id].status is SliceStatus.SELECTED
    assert rows[selected.id].score == 0.5  # never rescored
    assert (rows[edited.id].target_in, rows[edited.id].target_out) == (49.0, 71.0)
    assert rows["manual:1"].origin == "manual"


def test_scoring_failure_keeps_the_previous_candidates(tmp_path) -> None:
    source = _source()
    client = ScriptedClient(discovery=lambda _p: _two(source))
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        _score(lib, source, client)
        before = {s.id for s in lib.list_slices(profile_id="p")}
        with pytest.raises(RuntimeError, match="scoring down"):
            _score(lib, source, ScriptedClient(lambda _p: _two(source)), BoomScorer())
        assert {s.id for s in lib.list_slices(profile_id="p")} == before


def test_missing_credentials_keep_the_previous_candidates(tmp_path) -> None:
    source = _source()
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        _score(lib, source, ScriptedClient(discovery=lambda _p: _two(source)))
        before = {s.id for s in lib.list_slices(profile_id="p")}
        denied = ScriptedClient(discovery=Sequenced(MissingCredentialsError("x")))
        with pytest.raises(MissingCredentialsError):
            _score(lib, source, denied)
        assert {s.id for s in lib.list_slices(profile_id="p")} == before


def test_valid_empty_discovery_replaces_unprotected_candidates(tmp_path) -> None:
    source = _source()
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        _score(lib, source, ScriptedClient(discovery=lambda _p: _two(source)))
        slices, scorer, report = _score(lib, source, ScriptedClient(lambda _p: "[]"))
        assert slices == [] and lib.list_slices(profile_id="p") == []
    assert scorer.batches == [] and report.route == "full"


def test_other_profiles_are_never_touched(tmp_path) -> None:
    source = _source()
    other = BeatProfile(version="v1", name="o", brief="b", id="other")
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        _score(
            lib, source, ScriptedClient(discovery=lambda _p: _two(source)), None, other
        )
        _score(lib, source, ScriptedClient(lambda _p: "[]"))
        assert len(lib.list_slices(profile_id="other")) == 2


def test_offline_path_never_calls_a_model_and_records_the_prefilter(
    tmp_path,
) -> None:
    from ricesearcher.score.heuristic_scorer import HeuristicScorer

    source = _source()
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        slices = extract_and_score(
            source, profile=PROFILE, scorer=HeuristicScorer(), library=lib
        )
    assert slices
    assert {tuple(s.extraction["methods"]) for s in slices} == {
        ("heuristic-prefilter",)
    }
    assert {s.scorer_model for s in slices} == {"heuristic-offline"}


def _v4_database(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    for version in (1, 2, 3, 4):
        conn.executescript(MIGRATIONS[version])
    conn.execute("INSERT INTO meta VALUES ('schema_version', '4')")
    conn.execute(
        "INSERT INTO sources (id, kind, ref, media_path) "
        "VALUES ('src', 'local', 'r', '/m.mp4')"
    )
    conn.execute(
        "INSERT INTO candidate_slices (id, source_id, pad_in, pad_out, target_in, "
        "target_out, transcript_span, score, scorer_model, profile_id, status) "
        "VALUES ('src:p:1000-2000', 'src', 0, 3, 1, 2, 'old', 0.7, 'm', 'p', "
        "'selected')"
    )
    conn.commit()
    conn.close()


def test_v4_rows_migrate_with_unrecorded_provenance(tmp_path) -> None:
    db = tmp_path / "lib.sqlite3"
    _v4_database(db)
    with Library(db) as lib:
        [row] = lib.list_slices(profile_id="p")
    assert row.id == "src:p:1000-2000"
    assert (row.score, row.status, row.transcript_span) == (
        0.7,
        SliceStatus.SELECTED,
        "old",
    )
    assert row.extraction == {}  # legacy rows never claim the new method


def test_unreadable_provenance_reads_as_not_recorded(tmp_path) -> None:
    db = tmp_path / "lib.sqlite3"
    with Library(db) as lib:
        lib.upsert_source(_source())
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="x",
                    source_id="src",
                    pad_in=0,
                    pad_out=1,
                    target_in=0,
                    target_out=1,
                    transcript_span="t",
                    profile_id="p",
                )
            ]
        )
    conn = sqlite3.connect(db)
    conn.execute("UPDATE candidate_slices SET extraction = '[1'")
    conn.commit()
    conn.close()
    with Library(db) as lib:
        assert lib.get_slice("x").extraction == {}
