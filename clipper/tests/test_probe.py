from app.probe import _pick_duration


def test_prefers_video_stream_duration():
    assert _pick_duration({"duration": "12.5"}, {"duration": "13.0"}) == 12.5


def test_falls_back_to_format_duration():
    assert _pick_duration({}, {"duration": "9.0"}) == 9.0


def test_returns_zero_when_no_parseable_duration():
    # probe() turns this into a ProbeError (a 0-length header would be invisible).
    assert _pick_duration({}, {}) == 0.0
    assert _pick_duration({"duration": "N/A"}, {"duration": None}) == 0.0
