from tests._util import words

from transcribe.phrasing import group_words


def test_splits_on_max_words():
    ws = words(
        ("one", 0.0, 0.2), ("two", 0.2, 0.4), ("three", 0.4, 0.6),
        ("four", 0.6, 0.8), ("five", 0.8, 1.0), ("six", 1.0, 1.2),
    )
    phrases = group_words(ws, max_words=5, max_gap=10.0)
    assert [len(p.words) for p in phrases] == [5, 1]


def test_splits_on_gap():
    ws = words(
        ("hello", 0.0, 0.3), ("there", 0.3, 0.6),
        ("now", 2.0, 2.3), ("go", 2.3, 2.6),  # 1.4s gap before "now"
    )
    phrases = group_words(ws, max_words=5, max_gap=0.7)
    assert [p.text for p in phrases] == ["hello there", "now go"]


def test_drops_blank_tokens():
    ws = words(("a", 0.0, 0.1), ("  ", 0.1, 0.2), ("b", 0.2, 0.3))
    phrases = group_words(ws)
    assert phrases[0].text == "a b"


def test_empty_input():
    assert group_words([]) == []


def test_phrase_bounds():
    ws = words(("a", 1.0, 1.2), ("b", 1.2, 1.5))
    phrase = group_words(ws)[0]
    assert phrase.start == 1.0
    assert phrase.end == 1.5
