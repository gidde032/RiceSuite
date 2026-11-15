"""Online boundaries through scoring, persistence and migration (RiceSuite #23)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.extract.boundaries import BoundaryReport
from ricesearcher.handoff.writer import _entry_for
from ricesearcher.library.store import MIGRATIONS, Library
from ricesearcher.models import (
    CandidateSlice,
    SliceStatus,
    Source,
    SourceKind,
    TranscriptWord,
)
from ricesearcher.pipeline import SCORE_BATCH, extract_and_score
from ricesearcher.score.anthropic_scorer import (
    AnthropicScorer,
    MissingCredentialsError,
    ModelReply,
)
from ricesearcher.score.base import ScoredResult
from ricesearcher.score.usage import CallUsage
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


def test_unordered_source_word_text_matches_scoring_and_handoff(tmp_path: Path) -> None:
    """Boundary planning may normalize starts, but source text stays canonical."""
    source = Source(
        id="src",
        kind=SourceKind.LOCAL,
        ref="fixture",
        media_path="/m.mp4",
        duration_s=1.5,
        words=[
            # The final word starts before the preceding word in raw ASR output.
            # It still intersects the discovered [0.0, 0.95] interval.
            TranscriptWord("setup", 0.0, 0.9),
            TranscriptWord("next", 1.0, 1.5),
            TranscriptWord("overlap", 0.8, 1.4),
        ],
    )
    ws = source.words
    client = ScriptedClient(
        discovery=lambda _p: json.dumps([moment(0.0, 0.9)]),
        refinement=lambda _p: refined(0, 0),
    )
    scorer = RecordingScorer()
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        [scored] = extract_and_score(
            source,
            profile=PROFILE,
            scorer=scorer,
            library=lib,
            model_client=client,
            report=BoundaryReport(),
        )
    with Library(tmp_path / "lib.sqlite3") as lib:
        [stored] = lib.list_slices(profile_id=PROFILE.id)
        stored_source = lib.get_source(source.id)
        assert stored_source is not None
        handoff = _entry_for(stored, stored_source, 1)

    expected = "setup overlap"
    assert scorer.batches == [[expected]]
    assert scored.transcript_span == expected
    assert stored.transcript_span == expected
    assert handoff.transcript == expected
    assert [word.start for word in stored_source.words] == [0.0, 1.0, 0.8]
    assert ws[2].start == 0.8


def test_scoring_retry_marks_anthropic_usage_attempts_and_summary(
    tmp_path: Path, monkeypatch
) -> None:
    """Application retries are counted separately from SDK transport retries."""
    source = _source()
    ws = source.words
    client = ScriptedClient(
        discovery=lambda _p: json.dumps([moment(ws[0].start, ws[39].end)])
    )
    report = BoundaryReport()
    calls: list[CallUsage] = []

    def request(
        model: str,
        prompt: str,
        *,
        stage: str,
        usage_log: list[CallUsage] | None = None,
    ) -> ModelReply:
        record = CallUsage(stage=stage, model=model)
        calls.append(record)
        assert usage_log is not None
        usage_log.append(record)
        if len(calls) == 1:
            record.error = "RuntimeError"
            raise RuntimeError("transient scoring failure")
        return ModelReply(
            text='[{"index": 0, "score": 0.5, "rationale": "fixture"}]',
            usage=record,
        )

    monkeypatch.setattr("ricesearcher.score.anthropic_scorer.request", request)
    scorer = AnthropicScorer(model="fixture", usage_log=report.usage)
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        extract_and_score(
            source,
            profile=PROFILE,
            scorer=scorer,
            library=lib,
            model_client=client,
            report=report,
        )

    assert [(call.error, call.attempt) for call in calls] == [
        ("RuntimeError", 1),
        ("", 2),
    ]
    assert report.summary()["usage"]["scoring"]["retries"] == 1


def test_coalesced_duplicates_are_scored_once_with_both_proposals(tmp_path) -> None:
    source = _source()
    ws = source.words
    overlapping = json.dumps(
        [moment(ws[0].start, ws[39].end), moment(ws[10].start, ws[49].end)]
    )
    client = ScriptedClient(
        discovery=lambda _p: overlapping, refinement=lambda _p: refined(0, 49)
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


class FlakyScorer(RecordingScorer):
    def __init__(self, failures: int) -> None:
        super().__init__()
        self.failures = failures

    def score(self, windows, profile):
        if self.failures:
            self.failures -= 1
            self.batches.append([])
            raise RuntimeError("malformed scores")
        return super().score(windows, profile)


def test_a_failed_scoring_request_gets_one_application_retry(tmp_path) -> None:
    """Correctness MEDIUM: SPEC §14.1 bounds every model request to one retry."""
    source = _source()
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        client = ScriptedClient(discovery=lambda _p: _two(source))
        slices, scorer, _ = _score(lib, source, client, FlakyScorer(1))
        assert len(slices) == 2 and len(scorer.batches) == 2

        before = {s.id for s in lib.list_slices(profile_id="p")}
        twice = FlakyScorer(2)
        with pytest.raises(RuntimeError, match="malformed scores"):
            _score(lib, source, ScriptedClient(lambda _p: _two(source)), twice)
        assert len(twice.batches) == 2  # bounded: no third request
        assert {s.id for s in lib.list_slices(profile_id="p")} == before


def test_missing_credentials_during_scoring_are_not_retried(tmp_path) -> None:
    class Denied(RecordingScorer):
        def score(self, windows, profile):
            self.batches.append([])
            raise MissingCredentialsError("no key")

    source = _source()
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        denied = Denied()
        with pytest.raises(MissingCredentialsError):
            _score(lib, source, ScriptedClient(lambda _p: _two(source)), denied)
    assert len(denied.batches) == 1


def test_refinement_cannot_merge_two_distinct_moments(tmp_path) -> None:
    """Skeptic MEDIUM: widening both proposals into one story is rejected."""
    source = _source()
    client = ScriptedClient(
        discovery=lambda _p: _two(source), refinement=lambda _p: refined(20, 119)
    )
    with Library(tmp_path / "lib.sqlite3") as lib:
        lib.upsert_source(source)
        slices, _, _ = _score(lib, source, client)
    assert len(slices) == 2
    assert {s.extraction["refinement"][0] for s in slices} == {"refinement_failed"}
