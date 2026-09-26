"""Unit tests for the early-frame grab (SPEC §6.2 / §9).

``run_owned`` is faked so ffmpeg is never actually invoked — the tests exercise
the command shape, the seek bound, cleanup, and every degrade-to-error path.
"""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from app.probe import MediaInfo
from app.process import ProcessTimeoutError
from render import frame


class _Result:
    pass


def _install_run(monkeypatch, *, write=b"\xff\xd8jpeg", fail=False, timeout=False):
    captured: dict = {}

    def run(args, **kwargs):
        captured["args"] = list(args)
        if timeout:
            raise ProcessTimeoutError(list(args), 1.0)
        if fail:
            raise OSError("ffmpeg missing")
        Path(args[-1]).write_bytes(write)
        return _Result()

    monkeypatch.setattr(frame, "run_owned", run)
    return captured


def test_grab_frame_returns_base64_jpeg(monkeypatch, tmp_path):
    _install_run(monkeypatch)
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")
    out = frame.grab_frame_b64(src, MediaInfo(1080, 1920, 5.0, True), tmp_path)
    assert base64.b64decode(out) == b"\xff\xd8jpeg"
    assert not (tmp_path / frame.FRAME_NAME).exists()


def test_seek_point_is_bounded_for_a_tiny_clip(monkeypatch, tmp_path):
    captured = _install_run(monkeypatch)
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    frame.grab_frame_b64(src, MediaInfo(100, 100, 0.4, False), tmp_path)
    args = captured["args"]
    seek = args[args.index("-ss") + 1]
    assert float(seek) == pytest.approx(0.2)


def test_seek_point_caps_at_one_second_for_a_long_clip(monkeypatch, tmp_path):
    captured = _install_run(monkeypatch)
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    frame.grab_frame_b64(src, MediaInfo(1080, 1920, 40.0, True), tmp_path)
    args = captured["args"]
    assert float(args[args.index("-ss") + 1]) == pytest.approx(1.0)


def test_ffmpeg_failure_raises_frame_grab_error(monkeypatch, tmp_path):
    _install_run(monkeypatch, fail=True)
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    (tmp_path / frame.FRAME_NAME).write_bytes(b"stale")
    with pytest.raises(frame.FrameGrabError):
        frame.grab_frame_b64(src, MediaInfo(1080, 1920, 5.0, False), tmp_path)
    assert not (tmp_path / frame.FRAME_NAME).exists()


def test_timeout_raises_frame_grab_error(monkeypatch, tmp_path):
    _install_run(monkeypatch, timeout=True)
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    (tmp_path / frame.FRAME_NAME).write_bytes(b"stale")
    with pytest.raises(frame.FrameGrabError):
        frame.grab_frame_b64(src, MediaInfo(1080, 1920, 5.0, False), tmp_path)
    assert not (tmp_path / frame.FRAME_NAME).exists()


def test_empty_frame_file_raises_frame_grab_error(monkeypatch, tmp_path):
    _install_run(monkeypatch, write=b"")
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")
    with pytest.raises(frame.FrameGrabError):
        frame.grab_frame_b64(src, MediaInfo(1080, 1920, 5.0, False), tmp_path)
    assert not (tmp_path / frame.FRAME_NAME).exists()


def test_keyboard_interrupt_removes_partial_frame(monkeypatch, tmp_path):
    """Issue #18 (MEDIUM): interruption must not strand the producer's frame."""

    def interrupt(args, **kwargs):
        Path(args[-1]).write_bytes(b"partial")
        raise KeyboardInterrupt

    monkeypatch.setattr(frame, "run_owned", interrupt)
    src = tmp_path / "s.mp4"
    src.write_bytes(b"x")

    with pytest.raises(KeyboardInterrupt):
        frame.grab_frame_b64(src, MediaInfo(1080, 1920, 5.0, False), tmp_path)

    assert not (tmp_path / frame.FRAME_NAME).exists()


def test_cleanup_failure_does_not_mask_frame_result(monkeypatch, tmp_path):
    """Issue #18 (MEDIUM): cleanup failure must not break header generation."""

    _install_run(monkeypatch)
    src = tmp_path / "source.mp4"
    src.write_bytes(b"x")

    def refuse_unlink(self, *, missing_ok=False):
        raise OSError("filesystem cleanup failed")

    monkeypatch.setattr(frame.Path, "unlink", refuse_unlink)

    out = frame.grab_frame_b64(src, MediaInfo(1080, 1920, 5.0, True), tmp_path)

    assert base64.b64decode(out) == b"\xff\xd8jpeg"
