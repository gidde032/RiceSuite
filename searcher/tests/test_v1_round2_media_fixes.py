"""Round-2 regressions for review terminality and media-window timing."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ricesearcher.config import Config
from ricesearcher.handoff.extract import FfmpegClipExtractor
from ricesearcher.library.store import Library
from ricesearcher.models import CandidateSlice, SliceStatus, Source, SourceKind
from ricesearcher.web.app import create_app


def _ffmpeg(*args: str) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", *args],
        check=True,
    )


def _probe_streams(path: Path) -> list[dict[str, object]]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,codec_name,start_time,duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)["streams"]


def _probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def _make_av_source(root: Path, video_offset: float) -> Path:
    video = root / "video.mp4"
    audio = root / "audio.m4a"
    source = root / "source.mp4"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=duration=14:size=160x90:rate=10",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-g",
        "20",
        "-keyint_min",
        "20",
        "-sc_threshold",
        "0",
        "-bf",
        "2",
        str(video),
    )
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        r"aevalsrc=if(lt(mod(t\,2)\,1)\,0.5*sin(2*PI*440*t)\,0):s=48000:d=14",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        str(audio),
    )
    mux_args = [
        "-i",
        str(video),
        "-i",
        str(audio),
        "-map",
        "0:v:0",
        "-map",
        "1:a:0",
        "-c",
        "copy",
        "-shortest",
    ]
    if video_offset:
        mux_args[0:0] = ["-itsoffset", f"{video_offset:.3f}"]
        mux_args.extend(["-copyts", "-avoid_negative_ts", "disabled"])
    _ffmpeg(*mux_args, str(source))
    return source


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg and ffprobe are required for the media timing regression",
)
@pytest.mark.parametrize("video_offset", [0.0, 0.5])
def test_stream_copy_window_preserves_av_timeline(video_offset: float) -> None:
    """HIGH: a positive video offset must not drop audio at the clip start."""
    with tempfile.TemporaryDirectory(
        dir="/private/tmp", prefix="ricesearcher-round2-test-"
    ) as raw_root:
        root = Path(raw_root)
        source = _make_av_source(root, video_offset)
        for start, end, expected_silence, expected_video_start in (
            (0.0, 2.0, 1.0, video_offset),
            (2.3, 3.5, 0.7, 0.0),
            (12.3, 13.5, 0.7, 0.0),
        ):
            dest = root / f"clip-{start:.1f}.mp4"
            FfmpegClipExtractor().extract(source, start, end, dest)

            streams = _probe_streams(dest)
            by_type = {stream["codec_type"]: stream for stream in streams}
            assert by_type["audio"]["codec_name"] == "aac"
            assert by_type["video"]["codec_name"] == "h264"
            assert math.isclose(
                float(by_type["audio"]["start_time"]), 0.0, abs_tol=0.03
            ), f"{video_offset=} {start=}"
            assert math.isclose(
                float(by_type["audio"]["duration"]), end - start, abs_tol=0.05
            )
            assert math.isclose(
                float(by_type["video"]["start_time"]),
                expected_video_start,
                abs_tol=0.03,
            )
            assert math.isclose(_probe_duration(dest), end - start, abs_tol=0.05)

            decoded = subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-i",
                    str(dest),
                    "-map",
                    "0:a:0",
                    "-af",
                    "silencedetect=noise=-35dB:d=0.1",
                    "-f",
                    "null",
                    "-",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            silence_start = next(
                float(line.split("silence_start:", 1)[1].strip())
                for line in decoded.stderr.splitlines()
                if "silence_start:" in line
            )
            assert math.isclose(silence_start, expected_silence, abs_tol=0.05)


def test_handed_off_window_is_terminal_and_unchanged(tmp_path: Path) -> None:
    """HIGH: a stale review client cannot mutate manifested target bounds."""
    cfg = Config(data_dir=tmp_path / "data", handoff_dir=tmp_path / "handoff")
    cfg.ensure_dirs()
    with Library(cfg.db_path) as lib:
        lib.upsert_source(
            Source(
                id="src1",
                kind=SourceKind.LOCAL,
                ref="/source.mp4",
                media_path="/source.mp4",
            )
        )
        lib.upsert_slices(
            [
                CandidateSlice(
                    id="sl1",
                    source_id="src1",
                    pad_in=0,
                    pad_out=10,
                    target_in=2,
                    target_out=8,
                    transcript_span="a moment",
                    status=SliceStatus.HANDED_OFF,
                )
            ]
        )

    response = TestClient(create_app(cfg)).patch(
        "/api/slices/sl1/window",
        json={"target_in": 3, "target_out": 7},
    )

    assert response.status_code == 409
    assert "terminal" in response.json()["detail"]
    with Library(cfg.db_path) as lib:
        current = lib.get_slice("sl1")
        assert current is not None
        assert (current.target_in, current.target_out) == (2, 8)
