"""Cold-review repairs for the ✨ Suggest emoji 502 (RiceSuite #66, PR #72).

Every Suggest emoji call returned 502 "emoji generation failed": the model
thinks by default, its reply starts with a ``thinking`` block, and the call
site read ``content[0].text``. Each test failed before its fix. Tags are the
finding numbers in the PR #72 triage comment.
"""

import logging

import anthropic
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import anthropic_text, emoji_gen, header_gen, jobs, main, models
from app.models import EmojiRequest, HeaderRequest, Word
from app.probe import MediaInfo
from tests._anthropic_fakes import FakeClient, reply, status_error, text, thinking

PICKS = '{"picks": [{"word": 1, "emoji": ["🍕"]}]}'


def _words(*texts):
    return [Word(text=t, start=i * 0.3, end=i * 0.3 + 0.3) for i, t in enumerate(texts)]


def _suggest(client):
    return emoji_gen.suggest_emoji(_words("so", "pizza", "night"), client=client)


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
    job.words = _words("so", "pizza", "night")
    return job


@pytest.fixture
def model_client(monkeypatch):
    """Route the endpoints' owned client to a fake: ``model_client(result)``."""

    def use(result):
        client = FakeClient(result)
        monkeypatch.setenv(anthropic_text.API_KEY_ENV, "sk-ant-test")
        monkeypatch.setattr(anthropic_text, "_create_client", lambda key: client)
        monkeypatch.setattr(header_gen, "_create_client", lambda key: client)
        return client

    return use


# --- 1: read the reply's text blocks by type ---------------------------------


def test_1_a_thinking_first_reply_still_returns_picks():
    picks = _suggest(FakeClient(reply(thinking(), text(PICKS))))
    assert [(p.word, p.emoji) for p in picks] == [(1, ["🍕"])]


def test_1_a_thinking_first_reply_still_returns_a_header():
    client = FakeClient(reply(thinking(), text("  A header 🎉 ")))
    assert header_gen.generate_headers("a transcript", client=client) == ["A header 🎉"]


def test_1_a_reply_that_ran_out_of_tokens_while_thinking_names_max_tokens():
    client = FakeClient(reply(thinking(), stop_reason="max_tokens"))
    with pytest.raises(emoji_gen.EmojiGenerationError, match="max_tokens"):
        _suggest(client)


def test_1_an_empty_reply_names_its_stop_reason():
    with pytest.raises(emoji_gen.EmojiGenerationError, match="refusal"):
        _suggest(FakeClient(reply(stop_reason="refusal")))


def test_1_the_wrapped_error_names_the_cause_type():
    with pytest.raises(emoji_gen.EmojiGenerationError, match="RuntimeError"):
        _suggest(FakeClient(RuntimeError("api down")))


def test_1_the_endpoint_returns_picks_for_a_thinking_first_reply(
    isolated_jobs, model_client
):
    # Through the real picker and call site: only the client is a fake.
    job = _ready_job()
    model_client(reply(thinking(), text(PICKS)))
    out = main.suggest_emoji(job.id, EmojiRequest())
    assert out == {"picks": [{"word": 1, "emoji": ["🍕"]}]}


# --- 2: the budgets leave room for thinking ----------------------------------


def test_2_the_emoji_budget_leaves_room_for_thinking():
    client = FakeClient('{"picks": []}')
    _suggest(client)
    assert client.calls[0]["max_tokens"] >= 4096


def test_2_the_header_budget_leaves_room_for_thinking():
    client = FakeClient("A header")
    header_gen.generate_headers("a transcript", client=client)
    assert client.calls[0]["max_tokens"] >= 1024


# --- 3: the 502 log names its cause ------------------------------------------


def test_3_the_emoji_502_log_names_the_cause(isolated_jobs, model_client, caplog):
    job = _ready_job()
    model_client(RuntimeError("upstream said no"))
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as exc:
        main.suggest_emoji(job.id, EmojiRequest())
    assert exc.value.status_code == 502
    assert "RuntimeError('upstream said no')" in caplog.text


def test_3_the_header_502_log_names_the_cause(isolated_jobs, model_client, caplog):
    job = _ready_job()
    model_client(RuntimeError("upstream said no"))
    with caplog.at_level(logging.WARNING), pytest.raises(HTTPException) as exc:
        main.generate_header(job.id, HeaderRequest())
    assert exc.value.status_code == 502
    assert "RuntimeError('upstream said no')" in caplog.text


# --- 5: configuration errors are 503s that say what to fix -------------------


@pytest.mark.parametrize(
    ("cls", "status", "names"),
    [
        (anthropic.AuthenticationError, 401, anthropic_text.API_KEY_ENV),
        (anthropic.PermissionDeniedError, 403, anthropic_text.API_KEY_ENV),
        (anthropic.NotFoundError, 404, anthropic_text.MODEL_ENV),
    ],
)
def test_5_a_key_or_model_error_is_a_503_naming_the_setting(
    isolated_jobs, model_client, cls, status, names
):
    job = _ready_job()
    model_client(status_error(cls, status))
    with pytest.raises(HTTPException) as exc:
        main.suggest_emoji(job.id, EmojiRequest())
    assert exc.value.status_code == 503
    assert names in exc.value.detail
    with pytest.raises(HTTPException) as exc:
        main.generate_header(job.id, HeaderRequest())
    assert exc.value.status_code == 503
    assert names in exc.value.detail


@pytest.mark.parametrize(
    ("cls", "status"),
    [(anthropic.BadRequestError, 400), (anthropic.RateLimitError, 429)],
)
def test_5_other_api_errors_stay_502(isolated_jobs, model_client, cls, status):
    job = _ready_job()
    model_client(status_error(cls, status))
    with pytest.raises(HTTPException) as exc:
        main.suggest_emoji(job.id, EmojiRequest())
    assert exc.value.status_code == 502


# --- 6: the first JSON object is parsed, not the widest brace span -----------


@pytest.mark.parametrize(
    "answer",
    [
        PICKS + "\nI skipped {filler} words.",
        "Shape {picks}:\n" + PICKS,
        "```json\n" + PICKS + "\n```\nNote: {done}",
    ],
)
def test_6_prose_with_braces_around_the_json_is_accepted(answer):
    picks = _suggest(FakeClient(answer))
    assert [(p.word, p.emoji) for p in picks] == [(1, ["🍕"])]


# --- 7: the picker's word list is capped -------------------------------------


def test_7_the_emoji_request_caps_its_words():
    cap = getattr(models, "WORDS_MAX", None)
    assert cap == 5000
    word = Word(text="a", start=0.0, end=0.1)
    assert len(EmojiRequest(words=[word] * cap).words) == cap
    with pytest.raises(ValidationError):
        EmojiRequest(words=[word] * (cap + 1))
