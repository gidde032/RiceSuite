"""Online discovery, refinement and coalescence (RiceSuite #23, SPEC §14)."""

from __future__ import annotations

import json
import math

import pytest

from ricesearcher.beat.profile import BeatProfile
from ricesearcher.extract import boundaries as b
from ricesearcher.models import TranscriptWord
from ricesearcher.score.anthropic_scorer import (
    AnthropicModelClient,
    MissingCredentialsError,
)
from tests.boundary_fakes import (
    ScriptedClient,
    Sequenced,
    context_ids,
    covers,
    moment,
    proposed_ids,
    reference_times,
    refined,
    words,
)

PROFILE = BeatProfile(version="v1", name="beat", brief="A test beat.", id="p")


def build(ws, client, *, end=None, profile=PROFILE):
    report = b.BoundaryReport()
    moments = b.build_moments(
        ws,
        profile,
        source_id="src",
        source_end=ws[-1].end if end is None and ws else (end or 0.0),
        client=client,
        report=report,
    )
    return moments, report


def span(first_line: int, last_line: int):
    """A discovery handler proposing reference lines ``first..last``."""

    def handler(prompt: str) -> str:
        refs = reference_times(prompt)
        return json.dumps([moment(refs[first_line][0], refs[last_line][1])])

    return handler


def at(start: float, end: float):
    """A discovery handler proposing ``start..end`` when the prompt shows it."""

    def handler(prompt: str) -> str:
        refs = reference_times(prompt)
        if refs[0][0] <= start and end <= refs[-1][1]:
            return json.dumps([moment(start, end)])
        return "[]"

    return handler


# -- Full route, references and length ----------------------------------------


def test_full_transcript_route_uses_four_word_references_then_refines():
    ws = words(200)  # 100 s
    client = ScriptedClient()
    moments, report = build(ws, client)

    assert report.route == "full"
    [prompt] = client.prompts("discovery")
    assert "[0.0-1.9] word0 word1 word2 word3" in prompt
    assert "[2.0-3.9] word4 word5 word6 word7" in prompt
    assert len(client.prompts("refinement")) == 1
    [m] = moments
    assert (m.window.start, m.window.end) == (0.0, ws[-1].end)
    assert m.window.text == " ".join(w.text for w in ws)
    x = m.extraction
    assert x["methods"] == ["model-full"]
    assert x["refinement"] == ["refined"]
    assert x["needs_review"] is False
    assert x["model"] == "fake-boundary-model"
    assert x["proposals"][0]["model_claim"]["label"] == "topic"


def test_moments_shorter_than_twelve_seconds_are_kept():
    ws = words(200)
    moments, _ = build(ws, ScriptedClient(discovery=span(0, 1)))
    [m] = moments
    assert m.window.end - m.window.start < 12
    assert m.window.text == " ".join(w.text for w in ws[:8])


def test_proposal_over_the_ceiling_is_rejected_and_retried():
    ws = words(400)  # 200 s
    handler = Sequenced(
        json.dumps([moment(0.0, 189.9)]),
        json.dumps([moment(0.0, 89.9)]),
    )
    client = ScriptedClient(discovery=handler)
    moments, report = build(ws, client)
    assert len(client.prompts("discovery")) == 2
    assert "over ceiling" in client.prompts("discovery")[1]
    [m] = moments
    assert m.window.end <= 90.0
    attempts = report.sections[0]["attempts"]
    assert attempts[0]["rejected"] == {"over_ceiling": 1}
    assert report.route == "full"


def test_long_moment_up_to_the_ceiling_is_allowed():
    ws = words(400)
    moments, _ = build(ws, ScriptedClient(discovery=at(0.0, 179.9)))
    [m] = moments
    assert 170 < m.window.end - m.window.start <= b.MAX_S


def test_valid_empty_discovery_stays_empty_without_fallback():
    client = ScriptedClient(discovery=lambda _p: "[]")
    moments, report = build(words(200), client)
    assert moments == []
    assert report.route == "full"
    assert report.fallback_sections == []
    assert len(client.calls) == 1


def test_supported_json_wrapping_is_tolerated():
    payload = json.dumps([moment(0.0, 9.9)])
    reply = f"A quoted line [12.0-15.5] first.\n```json\n{payload}\n```\nDone."
    moments, report = build(words(200), ScriptedClient(discovery=lambda _p: reply))
    assert len(moments) == 1
    assert report.sections[0]["status"] == "model"


# -- Retries, failures and chunk routing ---------------------------------------


def test_valid_first_attempt_proposal_survives_an_empty_retry():
    ws = words(200)
    first = json.dumps([moment(0.0, 9.9), moment(150.0, 160.0)])  # 2nd: off-range
    client = ScriptedClient(discovery=Sequenced(first, "[]"))
    moments, report = build(ws, client)
    assert report.route == "full"
    assert [round(m.window.start, 2) for m in moments] == [0.0]
    attempts = report.sections[0]["attempts"]
    assert [a["outcome"] for a in attempts] == ["partial", "ok"]


def test_failed_full_request_keeps_partial_proposals_and_uses_chunks():
    ws = words(1400)  # 700 s: fits the budget, so the full request is tried
    first = json.dumps([moment(0.0, 9.9), moment(1.0, 0.5)])  # 2nd is inverted
    handler = Sequenced(first, RuntimeError("overloaded"), then=lambda _p: "[]")
    client = ScriptedClient(discovery=handler)
    moments, report = build(ws, client)

    assert report.route == "full_failed_chunks"
    assert report.sections[0]["index"] == -1
    assert report.sections[0]["status"] == "failed"
    assert "RuntimeError: overloaded" in report.sections[0]["attempts"][1]["error"]
    assert [s["status"] for s in report.sections[1:]] == ["model"] * 2
    [m] = moments
    assert m.extraction["methods"] == ["model-full"]


def test_over_budget_transcript_uses_chunks_and_never_sends_an_oversized_prompt():
    ws = words(6000)  # 3000 s
    client = ScriptedClient(discovery=span(0, 4))
    moments, report = build(ws, client)

    assert report.full_prompt_bytes > b.PROMPT_BYTE_BUDGET
    assert report.route == "over_budget_chunks"
    assert all(len(p.encode()) <= b.PROMPT_BYTE_BUDGET for _s, p in client.calls)
    sections = b.plan_sections(ws)
    assert len(client.prompts("discovery")) == len(sections) > 1
    assert {s["status"] for s in report.sections} == {"model"}
    assert {tuple(m.extraction["methods"]) for m in moments} == {("model-chunk",)}


def test_sections_overlap_enough_for_any_moment_up_to_the_ceiling():
    ws = words(3000)
    sections = b.plan_sections(ws)
    for k in range(0, len(ws), 37):
        start = ws[k].start
        last = max(i for i, w in enumerate(ws) if w.end <= start + b.MAX_S)
        assert any(s.lo <= k and last < s.hi for s in sections), k


def test_cross_chunk_story_is_found_whole_by_the_section_that_contains_it():
    ws = words(6000)
    client = ScriptedClient(discovery=at(400.0, 559.9))
    moments, _ = build(ws, client)
    stories = [m for m in moments if m.window.start < 401 and m.window.end > 559]
    assert stories, "the 400-560 s story crosses the 480 s section edge"
    assert stories[0].extraction["methods"] == ["model-chunk"]


def test_failed_chunk_gets_identified_fallback_and_other_chunks_keep_results():
    ws = words(6000)
    first_section_failing = Sequenced(
        RuntimeError("down"), RuntimeError("down"), then=span(0, 4)
    )
    client = ScriptedClient(discovery=first_section_failing)
    moments, report = build(ws, client)

    statuses = [s["status"] for s in report.sections]
    assert statuses[0] == "fallback"
    assert set(statuses[1:]) == {"model"}
    assert report.fallback_sections == [0]
    fallback = [m for m in moments if "heuristic-fallback" in m.extraction["methods"]]
    assert fallback and all(m.window.start < 481 for m in fallback)
    assert any(m.extraction["methods"] == ["model-chunk"] for m in moments)
    assert "heuristic fallback in 1 sections" in report.describe()


def test_partial_chunk_keeps_valid_proposals_and_adds_fallback():
    ws = words(6000)
    partial = json.dumps([moment(0.0, 9.9), moment("x", 3)])
    client = ScriptedClient(
        discovery=Sequenced(partial, "not json", then=lambda _p: "[]")
    )
    moments, report = build(ws, client)
    assert report.sections[0]["status"] == "partial_fallback"
    methods = [m.extraction["methods"] for m in moments if m.window.start < 481]
    assert ["model-chunk"] in methods
    assert ["heuristic-fallback"] in methods


def test_prompts_over_the_budget_are_never_sent_and_fall_back():
    huge = BeatProfile(version="v1", name="beat", brief="x" * 70_000, id="p")
    client = ScriptedClient()
    moments, report = build(words(400), client, profile=huge)
    assert client.calls == []
    assert report.route == "over_budget_chunks"
    assert moments
    for m in moments:
        assert m.extraction["methods"] == ["heuristic-fallback"]
        assert m.extraction["refinement"] == ["refinement_failed"]
        assert m.extraction["needs_review"] is True
        errors = m.extraction["proposals"][0]["refinement"]["errors"]
        assert errors[0] == "prompt over the request budget; not sent"


def test_missing_credentials_raise_instead_of_falling_back():
    client = ScriptedClient(discovery=Sequenced(MissingCredentialsError("no key")))
    with pytest.raises(MissingCredentialsError):
        build(words(200), client)


def test_missing_credentials_during_refinement_raise():
    client = ScriptedClient(refinement=Sequenced(MissingCredentialsError("no key")))
    with pytest.raises(MissingCredentialsError):
        build(words(200), client)


def test_real_client_without_credentials_raises_before_any_request(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    usage: list = []
    with pytest.raises(MissingCredentialsError):
        AnthropicModelClient("m").complete("p", stage="discovery", usage_log=usage)
    assert usage == []


def test_usage_records_keep_stage_section_and_attempt():
    client = ScriptedClient(discovery=Sequenced("nope", span(0, 1)))
    _, report = build(words(200), client)
    discovery = [u for u in report.usage if u.stage == "discovery"]
    assert [(u.section, u.attempt) for u in discovery] == [(-1, 1), (-1, 2)]
    summary = report.summary()["usage"]
    assert summary["discovery"]["calls"] == 2
    assert summary["discovery"]["retries"] == 1
    assert summary["refinement"]["calls"] == 1


# -- Validation of proposals and transcripts -----------------------------------


def _section(ws):
    return b.Section(-1, 0, len(ws), ws[0].start, ws[-1].end, True, True)


@pytest.mark.parametrize(
    ("item", "reason"),
    [
        ("text", "malformed_item"),
        ({"start": True, "end": 5.0}, "malformed_item"),
        ({"start": float("nan"), "end": 5.0}, "malformed_item"),
        ({"start": 0.0}, "malformed_item"),
        ({"start": -5.0, "end": 5.0}, "out_of_range"),
        ({"start": 0.0, "end": 999.0}, "out_of_range"),
        ({"start": 5.0, "end": 2.0}, "inverted"),
        ({"start": 6.0, "end": 9.4}, "invented_start"),
        ({"start": 0.0, "end": 6.0}, "invented_end"),
    ],
)
def test_invalid_proposals_are_rejected_with_a_reason(item, reason):
    ws = words(30, gap_every=10, gap=5.0)  # pauses at 4.9-9.9 s and 14.9-19.9 s
    resolved = b._Words(ws, PROFILE, source_id="s", source_end=ws[-1].end)
    result = b.resolve_proposal(item, _section(ws), resolved, method=b.FULL, attempt=1)
    assert result == reason


def test_proposal_times_resolve_to_real_word_edges_with_silence_padding():
    ws = words(30, gap_every=10, gap=5.0)
    resolved = b._Words(ws, PROFILE, source_id="s", source_end=ws[-1].end)
    p = b.resolve_proposal(
        {"start": 9.8, "end": 14.8}, _section(ws), resolved, method=b.FULL, attempt=1
    )
    assert isinstance(p, b.Proposal)
    assert (p.first, p.stop) == (10, 20)
    assert (ws[10].start, ws[19].end) == (10.0, 14.9)
    assert p.target_in == pytest.approx(10.0 - b.LEAD_PAD_S)
    assert p.target_out == pytest.approx(ws[19].end + b.TAIL_PAD_S)


@pytest.mark.parametrize(
    "bad",
    [
        [TranscriptWord("a", float("nan"), 1.0)],
        [TranscriptWord("a", 0.0, math.inf)],
        [TranscriptWord("a", 2.0, 1.0)],
        [TranscriptWord("a", -1.0, 1.0)],
        [TranscriptWord("a", 2.0, 2.5), TranscriptWord("b", 1.0, 1.5)],
    ],
)
def test_invalid_transcript_timings_raise_a_clear_error(bad):
    with pytest.raises(ValueError, match="transcript word timings"):
        build(bad, ScriptedClient(), end=10.0)


def test_media_shorter_than_the_transcript_never_clips_the_last_word():
    ws = words(40)  # last word ends 19.9 s
    moments, _ = build(ws, ScriptedClient(), end=12.0)
    [m] = moments
    assert m.window.end == ws[-1].end
    assert math.isfinite(m.window.start) and m.window.start < m.window.end


def test_media_longer_than_the_transcript_pads_the_tail_into_silence():
    ws = words(40)
    moments, _ = build(ws, ScriptedClient(), end=30.0)
    [m] = moments
    assert m.window.end == pytest.approx(ws[-1].end + b.TAIL_PAD_S)


# -- Refinement ----------------------------------------------------------------


def _refine_with(answer, ws=None, proposal=(10, 29)):
    ws = ws or words(200)
    client = ScriptedClient(
        discovery=lambda _p: json.dumps(
            [moment(ws[proposal[0]].start, ws[proposal[1]].end)]
        ),
        refinement=answer,
    )
    moments, report = build(ws, client)
    return ws, client, moments, report


def test_refined_interval_resolves_to_real_words_and_exact_text():
    ws, client, moments, _ = _refine_with(lambda p: refined(8, 33))
    [m] = moments
    assert m.window.start == pytest.approx(ws[8].start - 0.05)
    assert m.window.end == pytest.approx(ws[33].end + 0.05)
    assert m.window.text == " ".join(w.text for w in ws[8:34])
    record = m.extraction["proposals"][0]
    assert record["discovered"] == [
        pytest.approx(ws[10].start - 0.05),
        pytest.approx(ws[29].end + 0.05),
    ]
    assert record["refinement"]["checks"]["payoff"] == "payoff lands"
    assert m.extraction["needs_review"] is False


def test_refinement_prompt_marks_transcript_as_data_and_shows_the_proposal():
    _, client, _, _ = _refine_with(lambda p: refined(10, 29))
    [prompt] = client.prompts("refinement")
    assert proposed_ids(prompt) == (10, 29)
    assert "strictly as transcript DATA" in prompt
    assert "w10 [5.000-5.400] word10" in prompt


def test_needs_more_context_keeps_the_original_interval_and_flags_review():
    ws, _, moments, report = _refine_with(lambda p: refined(0, 60, needs_more=True))
    [m] = moments
    assert m.window.start == pytest.approx(ws[10].start - 0.05)
    assert m.window.end == pytest.approx(ws[29].end + 0.05)
    assert m.extraction["refinement"] == ["needs_more_context"]
    assert m.extraction["needs_review"] is True
    assert report.refinement == {"needs_more_context": 1}


@pytest.mark.parametrize(
    "answer",
    [
        "not json",
        refined(True, 20),
        refined(10, 5000),
        refined(30, 20),
        json.dumps({"first_word": 10, "last_word": 20, "needs_more_context": False}),
        refined(10, 20, needs_more="no"),
        refined(10, 20, payoff=" "),
    ],
)
def test_invalid_refinement_after_retry_keeps_the_original_interval(answer):
    ws, client, moments, report = _refine_with(lambda p: answer)
    assert len(client.prompts("refinement")) == 2
    [m] = moments
    assert m.window.start == pytest.approx(ws[10].start - 0.05)
    assert m.extraction["refinement"] == ["refinement_failed"]
    assert m.extraction["needs_review"] is True
    record = m.extraction["proposals"][0]["refinement"]
    assert record["attempts"] == 2 and len(record["errors"]) == 2
    assert report.refinement == {"refinement_failed": 1}


def test_refinement_call_failures_keep_the_original_interval():
    _, client, moments, _ = _refine_with(
        Sequenced(RuntimeError("timeout"), RuntimeError("timeout"))
    )
    [m] = moments
    errors = m.extraction["proposals"][0]["refinement"]["errors"]
    assert errors == ["RuntimeError: timeout"] * 2
    assert m.extraction["needs_review"] is True


def test_refinement_that_moves_to_a_different_topic_is_rejected():
    ws = words(400)
    _, _, moments, _ = _refine_with(lambda p: refined(190, 229), ws, (100, 139))
    [m] = moments
    assert m.window.start == pytest.approx(ws[100].start - 0.05)
    errors = m.extraction["proposals"][0]["refinement"]["errors"]
    assert errors == ["refinement moved to a different moment"] * 2


def test_refined_interval_over_the_ceiling_is_rejected():
    ws = words(800)
    _, _, moments, _ = _refine_with(lambda p: refined(0, 389), ws, (0, 299))
    [m] = moments
    assert m.extraction["refinement"] == ["refinement_failed"]
    assert "exceeds 180s" in m.extraction["proposals"][0]["refinement"]["errors"][0]


def test_retry_after_an_invalid_refinement_can_succeed_with_feedback():
    _, client, moments, _ = _refine_with(Sequenced("garbage", refined(10, 31)))
    assert "previous answer was rejected" in client.prompts("refinement")[1]
    [m] = moments
    assert m.extraction["refinement"] == ["refined"]
    assert m.extraction["proposals"][0]["refinement"]["attempts"] == 2


def test_refinement_context_is_bounded_in_seconds_and_words():
    ws = words(400)  # 0.5 s per word
    _, client, _, _ = _refine_with(lambda p: refined(150, 170), ws, (150, 170))
    lo, hi = context_ids(client.prompts("refinement")[0])
    assert ws[lo].end > ws[150].start - 0.05 - b.CONTEXT_S
    assert ws[hi].start < ws[170].end + 0.05 + b.CONTEXT_S
    assert lo > 0 and hi < len(ws) - 1

    dense = words(3000, step=0.05)
    _, client, _, _ = _refine_with(lambda p: refined(1200, 1400), dense, (1200, 1400))
    lo, hi = context_ids(client.prompts("refinement")[0])
    assert hi - lo + 1 == b.CONTEXT_WORDS
    assert lo < 1200 and hi > 1400


def test_moment_with_too_many_words_for_refinement_keeps_original_without_a_call():
    dense = words(1500, step=0.1)  # 150 s, 1,500 words
    _, client, moments, _ = _refine_with(lambda p: "unused", dense, (0, 1499))
    assert client.prompts("refinement") == []
    [m] = moments
    errors = m.extraction["proposals"][0]["refinement"]["errors"]
    assert errors == ["moment exceeds the refinement context budget"]
    assert m.extraction["needs_review"] is True


# -- Coalescence -----------------------------------------------------------------


def test_identical_final_intervals_coalesce_and_keep_every_proposal():
    ws = words(200)
    discovery = json.dumps(
        [
            moment(ws[0].start, ws[39].end, label="first"),
            moment(ws[10].start, ws[49].end, label="second"),
        ]
    )
    client = ScriptedClient(
        discovery=lambda _p: discovery, refinement=lambda _p: refined(0, 49)
    )
    moments, report = build(ws, client)
    [m] = moments
    labels = [p["model_claim"]["label"] for p in m.extraction["proposals"]]
    assert sorted(labels) == ["first", "second"]
    assert report.proposals == 2 and report.candidates == 1


def test_overlapping_and_nested_moments_stay_separate():
    ws = words(200)
    discovery = json.dumps(
        [moment(ws[0].start, ws[39].end), moment(ws[10].start, ws[19].end)]
    )
    moments, _ = build(ws, ScriptedClient(discovery=lambda _p: discovery))
    assert len(moments) == 2


def test_chunk_overlap_duplicates_with_identical_edges_coalesce():
    ws = words(6000)
    client = ScriptedClient(discovery=at(400.0, 459.9))  # inside two sections
    moments, report = build(ws, client)
    hits = [m for m in moments if 399 < m.window.start < 401]
    assert len(hits) == 1
    [record] = hits[0].extraction["proposals"]
    assert (record["section"], record["also_sections"]) == (0, [1])
    assert report.proposals == 1  # identical word ranges merge in discovery


def test_discovery_section_prompts_say_what_they_cover():
    ws = words(6000)
    client = ScriptedClient(discovery=lambda _p: "[]")
    build(ws, client)
    starts = [covers(p) for p in client.prompts("discovery")]
    assert starts == sorted(starts) and starts[0] == 0.0
    assert "another section covers it" in client.prompts("discovery")[1]
