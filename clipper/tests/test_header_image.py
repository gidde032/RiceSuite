from render.header_image import _segment, _wrap, has_emoji


def _fake_measure(word):
    # width == character count; parts unused by _wrap
    return (word, float(len(word)))


def test_wrap_honors_explicit_newlines_as_hard_breaks():
    # Regression: the emoji path split on " " only, mangling 2-line headers.
    lines = _wrap("line one\nline two", _fake_measure, space_w=1.0, max_width=1000)
    assert len(lines) == 2


def test_wrap_greedy_wraps_on_width():
    lines = _wrap("aaaa bbbb cccc", _fake_measure, space_w=1.0, max_width=9)
    assert len(lines) >= 2


def test_wrap_skips_blank_paragraphs():
    lines = _wrap("a\n\nb", _fake_measure, space_w=1.0, max_width=1000)
    assert len(lines) == 2


def test_has_emoji_detects_real_examples():
    assert has_emoji("anniversary pics \U0001F979")  # 🥹
    assert has_emoji("too funny \U0001F602")  # 😂
    assert has_emoji("❤️ love it")  # ❤️ with variation selector


def test_has_emoji_false_for_plain_text():
    assert not has_emoji("just a normal header")
    assert not has_emoji("")
    assert not has_emoji("numbers 123 and symbols !?.")


def test_segment_splits_text_and_emoji_runs():
    runs = _segment("pics\U0001F979")
    assert runs == [("text", "pics"), ("emoji", "\U0001F979")]


def test_segment_pure_text():
    assert _segment("hello") == [("text", "hello")]
