from render.header_image import _segment, has_emoji


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
