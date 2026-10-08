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


def test_the_prompt_sends_the_phrases_with_word_indices_and_asks_for_few():
    client = _Client('{"picks": []}')
    words = [*_words("so", "pizza", "", "night"), Word(text="later", start=5, end=6)]
    emoji_gen.suggest_emoji(words, client=client)
    (call,) = client.calls
    prompt = call["messages"][0]["content"]
    # group_words drops the empty word; indices stay global.
    assert "1. [0] so [1] pizza [3] night" in prompt
    assert "2. [4] later" in prompt
    system = call["system"]
    assert "one phrase in three" in system
    assert "JSON" in system
    assert call["max_tokens"] == emoji_gen.MAX_TOKENS


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
