import random

import pytest

from app import jobs
from app.models import Word
from transcribe.lyrics import MIN_WORD_S, align, normalize
from transcribe.phrasing import group_words


@pytest.fixture()
def isolated_jobs(tmp_path, monkeypatch):
    root = tmp_path / ".riceclipper_work"
    root.mkdir()
    monkeypatch.setattr(jobs, "WORK_ROOT", root)
    previous_jobs = jobs._JOBS.copy()
    jobs._JOBS.clear()
    yield root
    jobs._JOBS.clear()
    jobs._JOBS.update(previous_jobs)


def _words(*specs: tuple[str, float, float]) -> list[Word]:
    return [Word(text=t, start=s, end=e) for t, s, e in specs]


def _assert_invariants(words: list[Word], duration: float) -> None:
    for i, w in enumerate(words):
        assert w.end > w.start, f"word {i} end <= start"
        assert w.end - w.start >= MIN_WORD_S - 1e-9, f"word {i} too short"
        assert w.start >= -1e-9, f"word {i} start negative"
        if i > 0:
            assert w.start >= words[i - 1].end - 1e-9, f"word {i} overlaps {i - 1}"


# --- normalize ---------------------------------------------------------------


@pytest.mark.smoke
def test_normalize_strips_punctuation_keeps_apostrophe():
    assert normalize("Hello,") == "hello"
    assert normalize("don't") == "don't"
    assert normalize("...world!") == "world"


# --- exact match --------------------------------------------------------------


@pytest.mark.smoke
def test_exact_match():
    ref = _words(("hello", 1.0, 1.5), ("world", 1.5, 2.0))
    result = align("hello world", ref, 3.0)
    assert result.anchor_rate == 1.0
    assert result.method == "anchors"
    assert len(result.words) == 2
    assert result.words[0].start == 1.0
    assert result.words[1].end == 2.0
    _assert_invariants(result.words, 3.0)


# --- partial match ------------------------------------------------------------


def test_partial_match():
    ref = _words(
        ("the", 0.5, 0.7),
        ("sun", 0.7, 1.0),
        ("is", 1.0, 1.2),
        ("bright", 1.5, 2.0),
        ("today", 2.0, 2.5),
        ("now", 3.0, 3.5),
    )
    lyrics_text = "the sun is very bright today oh so very now"
    result = align(lyrics_text, ref, 4.0)
    assert result.method == "anchors"
    assert result.anchor_rate == 6 / 10
    for w in result.words:
        assert w.end > w.start
    _assert_invariants(result.words, 4.0)
    starts = [w.start for w in result.words]
    assert starts == sorted(starts)


# --- below threshold ----------------------------------------------------------


def test_below_threshold_even_fill():
    ref = _words(
        ("one", 1.0, 1.5),
    )
    lyrics_text = "one two three four five six seven eight nine ten"
    result = align(lyrics_text, ref, 5.0)
    assert result.method == "even_fill"
    assert result.anchor_rate == pytest.approx(1 / 10)
    _assert_invariants(result.words, 5.0)


# --- no reference words -------------------------------------------------------


def test_no_reference():
    result = align("hello world foo", [], 3.0)
    assert result.method == "even_fill"
    assert result.anchor_rate == 0.0
    assert result.words[0].start == pytest.approx(0.0)
    _assert_invariants(result.words, 3.0)


# --- punctuation and case -----------------------------------------------------


def test_punctuation_case_match():
    ref = _words(("hello", 1.0, 1.5), ("world", 2.0, 2.5))
    result = align("Hello, World!", ref, 3.0)
    assert result.words[0].text == "Hello,"
    assert result.words[1].text == "World!"
    assert result.words[0].start == 1.0
    assert result.words[1].start == 2.0
    _assert_invariants(result.words, 3.0)


# --- line_start and group_words -----------------------------------------------


def test_line_start_set():
    ref = _words(("a", 0.5, 1.0), ("b", 1.0, 1.5), ("c", 1.5, 2.0), ("d", 2.0, 2.5))
    result = align("a b\nc d", ref, 3.0)
    assert result.words[0].line_start is True
    assert result.words[1].line_start is False
    assert result.words[2].line_start is True
    assert result.words[3].line_start is False


def test_group_words_breaks_on_line_start():
    ref = _words(("a", 0.5, 1.0), ("b", 1.0, 1.5), ("c", 1.5, 2.0), ("d", 2.0, 2.5))
    result = align("a b\nc d", ref, 3.0)
    phrases = group_words(result.words, max_words=10, max_gap=10.0)
    assert len(phrases) == 2
    assert phrases[0].text == "a b"
    assert phrases[1].text == "c d"


# --- random invariant ---------------------------------------------------------


def test_random_invariants():
    rng = random.Random(42)
    tokens = [f"w{i}" for i in range(200)]
    lyrics_text = " ".join(tokens)
    ref_words: list[Word] = []
    t = 0.5
    for _i in range(80):
        dur = rng.uniform(0.1, 0.5)
        ref_words.append(Word(text=tokens[rng.randint(0, 199)], start=t, end=t + dur))
        t += dur + rng.uniform(0.0, 0.3)
    duration = t + 2.0
    result = align(lyrics_text, ref_words, duration)
    _assert_invariants(result.words, duration)
    assert len(result.words) == 200


# --- 2A-2 shortfall cascade past duration ------------------------------------


def test_shortfall_cascade_clamped_to_duration():
    ref = _words(("a", 0.90, 0.95), ("b", 0.96, 0.97))
    result = align("a x b", ref, 1.0)
    for w in result.words:
        assert w.end <= 1.0 + 1e-9, f"word {w.text!r} end {w.end} > duration"
    _assert_invariants(result.words, 1.0)


# --- empty raises -------------------------------------------------------------


def test_empty_lyrics_raises():
    with pytest.raises(ValueError):
        align("", [], 3.0)


def test_blank_lines_only_raises():
    with pytest.raises(ValueError):
        align("\n\n  \n", [], 3.0)


# --- endpoint tests ----------------------------------------------------------


def test_lyrics_endpoint_409_while_active(monkeypatch, isolated_jobs):
    from app import jobs as job_store
    from app import main

    job = job_store.create_job()
    job.status = "transcribing"
    with pytest.raises(Exception) as exc_info:
        main.lyrics_job(job.id, main.LyricsRequest(lyrics="hello"))
    assert exc_info.value.status_code == 409


def test_lyrics_endpoint_422_on_blank(monkeypatch, isolated_jobs):
    from app import jobs as job_store
    from app import main

    job = job_store.create_job()
    job.status = "ready"
    with pytest.raises(Exception) as exc_info:
        main.lyrics_job(job.id, main.LyricsRequest(lyrics=""))
    assert exc_info.value.status_code == 422


def test_lyrics_endpoint_success_replaces_words(monkeypatch, isolated_jobs):
    from app import jobs as job_store
    from app import main
    from app.models import Word as WordModel

    job = job_store.create_job()
    job.status = "ready"
    job.words = [WordModel(text="hello", start=1.0, end=1.5)]

    class FakeInfo:
        duration = 3.0

    job.info = FakeInfo()
    result = main.lyrics_job(job.id, main.LyricsRequest(lyrics="hello"))
    assert result.anchor_rate == 1.0
    assert job.words[0].text == "hello"
    assert job.words[0].line_start is True


def test_retranscribe_restores_whisper_words(monkeypatch, isolated_jobs):
    from app import jobs as job_store
    from app import main
    from app.models import Word as WordModel

    job = job_store.create_job()
    job.status = "ready"
    job.source_path = job.dir / "source.mp4"
    job.source_path.write_bytes(b"source")
    original = [WordModel(text="original", start=0.5, end=1.0)]
    job.words = [WordModel(text="lyric", start=0.5, end=1.0, line_start=True)]
    monkeypatch.setattr(main.whisper, "transcribe", lambda path: original)
    result = main.transcribe_job(job.id)
    assert result.words[0].text == "original"
    assert result.words[0].line_start is False
