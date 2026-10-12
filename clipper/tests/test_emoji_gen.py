"""The caption emoji picker and Clipper's one Anthropic call site (#66).

ADR-001 fact 1: Clipper has exactly one ``messages.create`` call. The header
generator and the emoji picker both go through ``app.anthropic_text``. No test
here reaches the network: every client is a fake.
"""

import json
import re
import threading
from pathlib import Path

import pytest
from fastapi import HTTPException

from app import anthropic_text, emoji_gen, header_gen, jobs, main
from app.models import EmojiPick, EmojiRequest, RenderRequest, Word
from app.probe import MediaInfo
from tests._anthropic_fakes import FakeClient as _Client

ROOT = Path(__file__).resolve().parents[1]


def _words(*texts):
    return [Word(text=t, start=i * 0.3, end=i * 0.3 + 0.3) for i, t in enumerate(texts)]


# --- one call site -----------------------------------------------------------


def test_clipper_has_exactly_one_messages_create_call():
    sources = [
        p for d in ("app", "render", "transcribe") for p in (ROOT / d).rglob("*.py")
    ]
    hits = [
        (p.relative_to(ROOT).as_posix(), n)
        for p in sources
        for n in [len(re.findall(r"\.messages\.create\(", p.read_text()))]
        if n
    ]
    assert hits == [("app/anthropic_text.py", 1)]


def test_the_header_and_the_picker_both_use_the_shared_helper(monkeypatch):
    seen = []
    real = anthropic_text.generate

    def spy(**kwargs):
        seen.append(kwargs["purpose"])
        return real(**kwargs)

    monkeypatch.setattr(anthropic_text, "generate", spy)
    header_gen.generate_headers("a transcript", client=_Client("A header"))
    emoji_gen.suggest_emoji(_words("pizza", "night"), client=_Client('{"picks": []}'))
    assert seen == ["header", "emoji"]


def test_without_a_key_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(
        anthropic_text, "_create_client", lambda key: pytest.fail("no client")
    )
    with pytest.raises(emoji_gen.EmojiConfigError):
        emoji_gen.suggest_emoji(_words("pizza"))


def test_an_owned_client_is_closed(monkeypatch):
    closed = []
    client = _Client('{"picks": []}')
    client.close = lambda: closed.append(True)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(anthropic_text, "_create_client", lambda key: client)
    assert emoji_gen.suggest_emoji(_words("pizza")) == []
    assert closed == [True]


# --- the prompt --------------------------------------------------------------


def test_the_prompt_sends_the_phrases_with_times_and_word_indices():
    client = _Client('{"picks": []}')
    words = [*_words("so", "pizza", "", "night"), Word(text="later", start=5, end=6)]
    emoji_gen.suggest_emoji(words, client=client)
    (call,) = client.calls
    prompt = call["messages"][0]["content"]
    # group_words drops the empty word; indices stay global.
    assert "1. (0.0s) [0] so [1] pizza [3] night" in prompt
    assert "2. (5.0s) [4] later" in prompt
    assert "JSON" in call["system"]
    assert call["max_tokens"] == emoji_gen.MAX_TOKENS


def test_the_prompt_asks_for_most_phrases_and_spaced_repeats():
    # #82: the old prompt capped picks at one phrase in three, so only ~25% of
    # phrases got emoji. The maintainer wants 80%+, skipping only filler.
    system = emoji_gen.SYSTEM_PROMPT
    assert "one phrase in" not in system
    assert "Most phrases get no emoji" not in system
    assert "at least four phrases in five" in system
    assert "filler" in system
    assert f"at most {emoji_gen.MAX_USES_PER_EMOJI} times" in system
    assert f"within {emoji_gen.REPEAT_GAP_S:g} seconds" in system
    # Skeptic review of #83: a used-up emoji should be swapped, not skipped,
    # and stock reaction emoji are named so they don't fill the gaps.
    assert "instead of skipping the phrase" in system
    assert "Avoid stock reaction emoji" in system


def test_the_suggest_button_no_longer_promises_a_few():
    html = (ROOT / "web" / "index.html").read_text()
    assert "suggests a few emoji" not in html
    assert "suggests emoji for most phrases" in html


def test_the_token_budget_fits_a_reply_for_every_phrase():
    # A pick costs about 20 output tokens; the model thinks first as well.
    assert emoji_gen.MAX_TOKENS >= 8000


def test_no_words_sends_nothing():
    client = _Client('{"picks": []}')
    assert (
        emoji_gen.suggest_emoji([Word(text=" ", start=0, end=1)], client=client) == []
    )
    assert client.calls == []


# --- parsing and validation --------------------------------------------------


def _parse(reply, words=None):
    return emoji_gen.suggest_emoji(
        words or _words("so", "pizza", "night", "then", "dogs", "and", "cats"),
        client=_Client(reply),
    )


def test_a_reply_becomes_picks_on_global_word_indices():
    picks = _parse(
        '{"picks": [{"word": 1, "emoji": ["🍕"]}, {"word": 6, "emoji": ["🐱", "😺"]}]}'
    )
    assert [(p.word, p.emoji) for p in picks] == [(1, ["🍕"]), (6, ["🐱", "😺"])]


def test_a_fenced_reply_is_accepted():
    picks = _parse(
        'Here you go:\n```json\n{"picks": [{"word": 4, "emoji": ["🐶"]}]}\n```'
    )
    assert [(p.word, p.emoji) for p in picks] == [(4, ["🐶"])]


def test_bad_items_are_dropped_and_the_first_pick_per_phrase_wins():
    words = [
        *_words("so", "pizza", "night"),
        Word(text="dogs", start=3.0, end=3.3),
        Word(text="bark", start=3.3, end=3.6),
    ]
    reply = json.dumps(
        {
            "picks": [
                {"word": 2, "emoji": ["🌙"]},
                {"word": 1, "emoji": ["🍕"]},  # same phrase: the earlier word wins
                {"word": 9, "emoji": ["🔥"]},  # no such word
                {"word": 3, "emoji": ["dog"]},  # not emoji
                {"word": 4, "emoji": ["🐶", "🔊", "🎉"]},  # three: first two kept
                {"word": "4", "emoji": ["🐶"]},
                "junk",
            ]
        }
    )
    picks = _parse(reply, words)
    assert [(p.word, p.emoji) for p in picks] == [(1, ["🍕"]), (4, ["🐶", "🔊"])]


def _spaced(*starts):
    """One single-word phrase per start time: starts 0.8s or more apart leave
    a silent gap longer than ``group_words``' 0.7s, which splits phrases."""
    return [Word(text=f"w{i}", start=t, end=t + 0.05) for i, t in enumerate(starts)]


def _picks(*emoji_lists):
    return json.dumps(
        {"picks": [{"word": i, "emoji": e} for i, e in enumerate(emoji_lists)]}
    )


def test_a_repeat_within_the_gap_is_dropped():
    gap = emoji_gen.REPEAT_GAP_S
    words = _spaced(0.0, 2.0, gap - 0.1, gap + 1.0)
    picks = _parse(_picks(["🍕"], ["🍕", "🌙"], ["🍕"], ["🍕"]), words)
    # 2.0s: 🍕 too soon, 🌙 kept. gap-0.1s: still too soon, the pick goes.
    # gap+1.0s: far enough from the 0.0s use.
    assert [(p.word, p.emoji) for p in picks] == [
        (0, ["🍕"]),
        (1, ["🌙"]),
        (3, ["🍕"]),
    ]


def test_an_emoji_is_used_at_most_the_capped_number_of_times():
    step = emoji_gen.REPEAT_GAP_S + 1
    n = emoji_gen.MAX_USES_PER_EMOJI + 2
    words = _spaced(*(i * step for i in range(n)))
    picks = _parse(_picks(*(["🐶"] for _ in range(n))), words)
    assert [p.word for p in picks] == list(range(emoji_gen.MAX_USES_PER_EMOJI))


def test_variation_selectors_do_not_make_a_new_emoji():
    words = _spaced(0.0, 1.0)
    picks = _parse(_picks(["❤️"], ["❤"]), words)
    assert [(p.word, p.emoji) for p in picks] == [(0, ["❤️"])]


def test_the_same_emoji_twice_in_one_pick_keeps_one():
    picks = _parse(_picks(["🔥", "🔥"]), _spaced(0.0))
    assert [(p.word, p.emoji) for p in picks] == [(0, ["🔥"])]


def test_the_picks_never_exceed_what_a_render_accepts(monkeypatch):
    monkeypatch.setattr(emoji_gen, "EMOJI_PICKS_MAX", 3)
    words = _spaced(*(i * 1.0 for i in range(6)))
    letters = "🍎🍌🍇🍉🍓🍒"
    picks = _parse(_picks(*([e] for e in letters)), words)
    assert [p.word for p in picks] == [0, 1, 2]


def test_a_repeat_exactly_at_the_gap_is_kept():
    words = _spaced(0.0, emoji_gen.REPEAT_GAP_S)
    picks = _parse(_picks(["🍕"], ["🍕"]), words)
    assert [p.word for p in picks] == [0, 1]


def test_the_gap_uses_the_times_the_prompt_shows():
    # The prompt shows 0.0s and 8.0s; the raw gap is 7.96s. The model was told
    # these are 8 seconds apart, so the code must agree (review of #83).
    words = _spaced(0.04, emoji_gen.REPEAT_GAP_S)
    picks = _parse(_picks(["🍕"], ["🍕"]), words)
    assert [p.word for p in picks] == [0, 1]


def test_a_blocked_anchor_falls_back_to_the_phrases_next_anchor():
    # Phrase 2 holds "pizza" and "moon"; its first anchor repeats 🍕 too soon.
    words = [
        Word(text="pizza", start=0.0, end=0.1),
        Word(text="pizza", start=2.0, end=2.1),
        Word(text="moon", start=2.2, end=2.3),
    ]
    picks = _parse(_picks(["🍕"], ["🍕"], ["🌙"]), words)
    assert [(p.word, p.emoji) for p in picks] == [(0, ["🍕"]), (2, ["🌙"])]


def test_dropped_emoji_do_not_take_a_pick_slot():
    picks = _parse(_picks(["🔥", "🔥", "🎉"]), _spaced(0.0))
    assert [p.emoji for p in picks] == [["🔥", "🎉"]]
    picks = _parse(_picks(["🍕"], ["🍕", "🌙", "🎉"]), _spaced(0.0, 1.0))
    assert [p.emoji for p in picks] == [["🍕"], ["🌙", "🎉"]]


@pytest.mark.parametrize(
    ("first", "second"),
    [("❤️", "❤\ufe0e"), ("👍", "👍🏽"), ("👍🏻", "👍🏿")],
)
def test_selectors_and_skin_tones_do_not_make_a_new_emoji(first, second):
    picks = _parse(_picks([first], [second]), _spaced(0.0, 1.0))
    assert [p.word for p in picks] == [0]


def test_the_cap_applies_after_spacing(monkeypatch):
    # Capping first would keep phrases 0 and 1, then lose 1 to spacing.
    monkeypatch.setattr(emoji_gen, "EMOJI_PICKS_MAX", 2)
    picks = _parse(_picks(["🍕"], ["🍕"], ["🌙"]), _spaced(0.0, 1.0, 2.0))
    assert [p.word for p in picks] == [0, 2]


@pytest.mark.parametrize("reply", ["no json here", '{"picks": 3}', "[1, 2]", ""])
def test_an_unusable_reply_is_a_generation_error(reply):
    with pytest.raises(emoji_gen.EmojiGenerationError):
        _parse(reply)


def test_an_empty_list_is_a_valid_answer():
    assert _parse('{"picks": []}') == []


# --- the endpoint ------------------------------------------------------------


@pytest.fixture
def isolated_jobs(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    previous = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous)


def _ready_job():
    job = jobs.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"x")
    job.info = MediaInfo(1080, 1920, 2.0, True)
    return job


def test_the_endpoint_returns_picks_without_holding_the_job_lock(
    monkeypatch, isolated_jobs
):
    # The model call can take seconds; job reads and other clips' renders must
    # not wait behind it. Probe the lock from another thread: the request
    # thread holds an RLock reentrantly, so a same-thread probe would pass.
    job = _ready_job()
    observed = {}

    def fake(words, **_kw):
        def probe():
            got = jobs._JOBS_LOCK.acquire(blocking=False)
            observed["free"] = got
            if got:
                jobs._JOBS_LOCK.release()

        thread = threading.Thread(target=probe)
        thread.start()
        thread.join()
        observed["words"] = [w.text for w in words]
        return [EmojiPick(word=0, emoji=["🍕"])]

    monkeypatch.setattr(main.emoji_gen, "suggest_emoji", fake)
    out = main.suggest_emoji(job.id, EmojiRequest(words=_words("pizza")))
    assert out == {"picks": [{"word": 0, "emoji": ["🍕"]}]}
    assert observed == {"free": True, "words": ["pizza"]}


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (emoji_gen.EmojiConfigError("no key"), 503),
        (emoji_gen.EmojiGenerationError("x"), 502),
    ],
)
def test_the_endpoint_fails_softly(monkeypatch, isolated_jobs, error, status):
    job = _ready_job()

    def boom(*_a, **_k):
        raise error

    monkeypatch.setattr(main.emoji_gen, "suggest_emoji", boom)
    with pytest.raises(HTTPException) as exc:
        main.suggest_emoji(job.id, EmojiRequest(words=_words("pizza")))
    assert exc.value.status_code == status


def test_the_endpoint_needs_a_ready_job(isolated_jobs):
    with pytest.raises(HTTPException) as exc:
        main.suggest_emoji("missing", EmojiRequest())
    assert exc.value.status_code == 404
    job = jobs.create_job()
    with pytest.raises(HTTPException) as exc:
        main.suggest_emoji(job.id, EmojiRequest())
    assert exc.value.status_code == 409


def test_rendering_with_emoji_never_calls_the_picker(monkeypatch, isolated_jobs):
    job = _ready_job()
    monkeypatch.setattr(
        main.emoji_gen, "suggest_emoji", lambda *a, **k: pytest.fail("sent")
    )
    monkeypatch.setattr(
        anthropic_text, "_create_client", lambda key: pytest.fail("client built")
    )
    monkeypatch.setattr(main, "render", lambda *a, **k: job.dir / "output.mp4")
    req = RenderRequest(
        words=_words("pizza"), emoji_on=True, emoji=[EmojiPick(word=0, emoji=["🍕"])]
    )
    assert main.render_job(job.id, req).status == "done"
