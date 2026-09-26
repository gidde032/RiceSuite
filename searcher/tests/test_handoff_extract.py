"""Focused regressions for exact handoff clip extraction."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ricesearcher.handoff import extract as extract_mod
from ricesearcher.handoff.extract import FfmpegClipExtractor


def test_ffmpeg_arguments_preserve_exact_window_precision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    dest = tmp_path / "clip.mp4"
    source.write_bytes(b"source")
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        commands.append(command)
        dest.write_bytes(b"clip")

    monkeypatch.setattr(extract_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(extract_mod, "ffprobe_duration", lambda _path: 1.0)

    FfmpegClipExtractor().extract(source, 1.23456, 2.34567, dest)

    assert commands == [
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-ss",
            "1.23456",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-t",
            "1.11111",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(dest),
        ]
    ]


@pytest.mark.parametrize("probe_result", [0.0, float("nan"), float("inf")])
def test_extraction_rejects_unusable_output_duration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    probe_result: float,
) -> None:
    source = tmp_path / "source.mp4"
    dest = tmp_path / "clip.mp4"
    source.write_bytes(b"source")

    def fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        dest.write_bytes(b"unusable")

    monkeypatch.setattr(extract_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(extract_mod, "ffprobe_duration", lambda _path: probe_result)

    with pytest.raises(RuntimeError, match="usable duration"):
        FfmpegClipExtractor().extract(source, 1.0, 2.0, dest)


def test_extraction_rejects_unprobeable_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source.mp4"
    dest = tmp_path / "clip.mp4"
    source.write_bytes(b"source")

    def fake_run(command: list[str], *, check: bool) -> None:
        assert check is True
        dest.write_bytes(b"unprobeable")

    def fail_probe(_path: Path) -> float:
        raise subprocess.CalledProcessError(1, ["ffprobe"])

    monkeypatch.setattr(extract_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(extract_mod, "ffprobe_duration", fail_probe)

    with pytest.raises(RuntimeError, match="could not be probed"):
        FfmpegClipExtractor().extract(source, 1.0, 2.0, dest)
