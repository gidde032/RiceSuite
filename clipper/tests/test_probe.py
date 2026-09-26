import json
from types import SimpleNamespace

import pytest

from app import probe as probe_module
from app.probe import ProbeError, _pick_duration
from app.process import ProcessTimeoutError


@pytest.mark.smoke
def test_prefers_video_stream_duration():
    assert _pick_duration({"duration": "12.5"}, {"duration": "13.0"}) == 12.5


def test_falls_back_to_format_duration():
    assert _pick_duration({}, {"duration": "9.0"}) == 9.0


def test_returns_zero_when_no_parseable_duration():
    # probe() turns this into a ProbeError (a 0-length header would be invisible).
    assert _pick_duration({}, {}) == 0.0
    assert _pick_duration({"duration": "N/A"}, {"duration": None}) == 0.0


def test_probe_rejects_non_finite_duration(monkeypatch):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1080,
                "height": 1920,
                "duration": "Infinity",
            }
        ],
        "format": {"duration": "Infinity"},
    }
    monkeypatch.setattr(
        probe_module,
        "run_owned",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    with pytest.raises(ProbeError, match="could not determine video duration"):
        probe_module.probe("source.mp4")


def test_probe_timeout_is_converted_to_probe_error(monkeypatch):
    def timed_out(*args, **kwargs):
        raise ProcessTimeoutError(args[0], kwargs["timeout"])

    monkeypatch.setattr(probe_module, "run_owned", timed_out)

    with pytest.raises(ProbeError, match="ffprobe timed out"):
        probe_module.probe("source.mp4")


def test_has_libass_timeout_is_converted_to_probe_error(monkeypatch):
    monkeypatch.setattr(probe_module.shutil, "which", lambda name: "/usr/bin/ffmpeg")

    def timed_out(*args, **kwargs):
        raise ProcessTimeoutError(args[0], kwargs["timeout"])

    monkeypatch.setattr(probe_module, "run_owned", timed_out)

    with pytest.raises(ProbeError, match="libass probe timed out"):
        probe_module.has_libass()


def test_probe_uses_bounded_timeout_and_preserves_json_contract(monkeypatch):
    calls = []
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1080,
                "height": 1920,
                "duration": "4.5",
            }
        ],
        "format": {},
    }

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(stdout=json.dumps(payload))

    monkeypatch.setattr(probe_module, "run_owned", fake_run)

    info = probe_module.probe("source.mp4")

    assert info.duration == 4.5
    assert calls[0][1]["timeout"] == 30.0
    assert calls[0][1]["check"] is True


def test_probe_normalizes_rotation_to_display_orientation(monkeypatch):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "sample_aspect_ratio": "1:1",
                "duration": "4.5",
                "side_data_list": [{"rotation": 90}],
            }
        ],
        "format": {},
    }
    monkeypatch.setattr(
        probe_module,
        "run_owned",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    info = probe_module.probe("source.mp4")

    assert (info.width, info.height) == (1080, 1920)
    assert (info.coded_width, info.coded_height) == (1920, 1080)
    assert info.rotation == 90


def test_probe_supports_legacy_rotation_tag(monkeypatch):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "duration": "4.5",
                "tags": {"rotate": "-90"},
            }
        ],
        "format": {},
    }
    monkeypatch.setattr(
        probe_module,
        "run_owned",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    info = probe_module.probe("source.mp4")

    assert (info.width, info.height) == (1080, 1920)
    assert info.rotation == 270


def test_probe_normalizes_sample_aspect_ratio_to_square_pixels(monkeypatch):
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 720,
                "height": 576,
                "sample_aspect_ratio": "64:45",
                "duration": "4.5",
            }
        ],
        "format": {},
    }
    monkeypatch.setattr(
        probe_module,
        "run_owned",
        lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(payload)),
    )

    info = probe_module.probe("source.mp4")

    assert (info.width, info.height) == (1024, 576)
    assert info.sample_aspect_ratio == pytest.approx(64 / 45)
