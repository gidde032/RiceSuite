import signal
import subprocess
from pathlib import Path

import pytest

from app import process
from app.models import MusicSettings, RenderRequest
from app.probe import MediaInfo
from render.pipeline import (
    _audio_graph,
    _encode_threads,
    _ffmpeg_command,
    _job_child,
    _render_timeout,
    RenderError,
    render,
)


def _req(mode, vol=0.5):
    return RenderRequest(music=MusicSettings(mode=mode, filename="music.m4a", volume=vol))


def test_replace_music_is_padded_so_shortest_cannot_truncate_video():
    # Regression: short music in replace mode used to cut the clip via -shortest.
    stmts, target = _audio_graph(_req("replace"), has_audio=True, has_music=True)
    assert target == "[aout]"
    assert any("apad" in s for s in stmts)


def test_mix_with_audio_is_bounded_by_amix_duration_first():
    stmts, target = _audio_graph(_req("mix"), has_audio=True, has_music=True)
    assert target == "[aout]"
    assert any("amix=inputs=2:duration=first" in s for s in stmts)


def test_mix_over_silent_source_pads_music():
    stmts, target = _audio_graph(_req("mix"), has_audio=False, has_music=True)
    assert target == "[aout]"
    assert any("apad" in s for s in stmts)


def test_audio_graph_pads_and_trims_original_audio_to_video_duration():
    stmts, target = _audio_graph(
        _req("none"), has_audio=True, has_music=False, duration=12.5
    )

    assert target == "[aout]"
    assert stmts == ["[0:a]apad,atrim=duration=12.5[aout]"]


def test_mix_graph_pads_each_input_and_trims_the_result():
    stmts, target = _audio_graph(_req("mix"), True, True, duration=12.5)

    assert target == "[aout]"
    assert "[0:a]apad,atrim=duration=12.5[orig]" in stmts
    assert "[1:a]volume=0.5,apad,atrim=duration=12.5[m]" in stmts
    assert any(
        "amix=inputs=2:duration=first:normalize=0,atrim=duration=12.5[aout]" in s
        for s in stmts
    )


def test_replace_graph_keeps_short_music_regression_and_exact_duration():
    stmts, target = _audio_graph(_req("replace"), True, True, duration=12.5)

    assert target == "[aout]"
    assert stmts == ["[1:a]volume=0.5,apad,atrim=duration=12.5[aout]"]


def test_encode_threads_defaults_to_half_logical_cpus(monkeypatch):
    monkeypatch.delenv("RICECLIPPER_FFMPEG_THREADS", raising=False)
    monkeypatch.setattr("render.pipeline.os.cpu_count", lambda: 8)

    assert _encode_threads() == 4


@pytest.mark.parametrize("override", ["7", "not-an-int", "0", "-2"])
def test_encode_threads_parses_positive_override_and_falls_back(monkeypatch, override):
    monkeypatch.setattr("render.pipeline.os.cpu_count", lambda: 6)
    monkeypatch.setenv("RICECLIPPER_FFMPEG_THREADS", override)

    assert _encode_threads() == (7 if override == "7" else 3)


def test_ffmpeg_command_uses_explicit_duration_bound_and_thread_cap(monkeypatch):
    monkeypatch.setattr("render.pipeline._encode_threads", lambda: 4)

    cmd = _ffmpeg_command(
        Path("source.mp4"), None, False, "[0:v]null[vout]", None, 12.5
    )

    assert "-shortest" in cmd
    assert cmd[cmd.index("-threads") + 1] == "4"
    assert cmd[cmd.index("-filter_threads") + 1] == "4"
    assert cmd[cmd.index("-filter_complex_threads") + 1] == "4"
    assert cmd[cmd.index("-t") + 1] == "12.5"


def test_render_timeout_scales_with_duration():
    assert _render_timeout(1.0) == 120.0
    assert _render_timeout(15.0) == 150.0


def test_render_timeout_is_converted_to_render_error(monkeypatch, tmp_path):
    def timed_out(*args, **kwargs):
        raise process.ProcessTimeoutError(args[0], kwargs["timeout"])

    monkeypatch.setattr("render.pipeline.run_owned", timed_out)

    with pytest.raises(RenderError, match="ffmpeg timed out"):
        render(
            tmp_path,
            tmp_path / "source.mp4",
            MediaInfo(width=1080, height=1920, duration=15.0, has_audio=False),
            RenderRequest(),
        )


def test_render_launch_error_is_converted_to_render_error(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "render.pipeline.run_owned",
        lambda *args, **kwargs: (_ for _ in ()).throw(FileNotFoundError("ffmpeg")),
    )

    with pytest.raises(RenderError, match="ffmpeg"):
        render(
            tmp_path,
            tmp_path / "source.mp4",
            MediaInfo(width=1080, height=1920, duration=15.0, has_audio=False),
            RenderRequest(),
        )


def test_render_resolves_request_visual_presets(monkeypatch, tmp_path):
    class Completed:
        returncode = 0
        stderr = ""

    monkeypatch.setattr("render.pipeline.run_owned", lambda *args, **kwargs: Completed())
    request = RenderRequest(
        header="A compact header",
        caption_style="punch",
        header_style="white_plate",
    )

    render(
        tmp_path,
        tmp_path / "source.mp4",
        MediaInfo(width=1080, height=1920, duration=15.0, has_audio=False),
        request,
    )

    ass = (tmp_path / "captions.ass").read_text()
    assert "Style: Caption,Impact,92" in ass
    assert "Style: Header,Arial,42" in ass
    assert ",3,16,0,8,80,80,450,1" in ass


def test_render_rejects_non_finite_duration(tmp_path):
    with pytest.raises(RenderError, match="invalid video duration"):
        render(
            tmp_path,
            tmp_path / "source.mp4",
            MediaInfo(width=1080, height=1920, duration=float("inf"), has_audio=False),
            RenderRequest(),
        )


def test_owned_process_kills_process_group_on_timeout(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 123
        returncode = -signal.SIGKILL

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            if timeout is not None:
                raise subprocess.TimeoutExpired(["ffmpeg"], timeout, output="partial")
            return "out", "err"

        def kill(self):
            calls.append(("kill",))

    fake = FakeProcess()
    popen_kwargs = {}
    monkeypatch.setattr(
        process.subprocess,
        "Popen",
        lambda args, **kwargs: popen_kwargs.update(kwargs) or fake,
    )
    monkeypatch.setattr(process.os, "getpgid", lambda pid: 456)
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda pgid, sig: calls.append(("killpg", pgid, sig)),
    )

    with pytest.raises(process.ProcessTimeoutError) as exc_info:
        process.run_owned(
            ["ffmpeg"], timeout=3.0, capture_output=True, text=True
        )

    assert "timed out" in str(exc_info.value)
    assert ("killpg", 456, signal.SIGKILL) in calls
    assert ("communicate", 3.0) in calls
    assert ("communicate", None) in calls
    assert popen_kwargs["start_new_session"] is True
    assert popen_kwargs["stdout"] is subprocess.PIPE
    assert popen_kwargs["stderr"] is subprocess.PIPE


def test_explicit_owned_process_termination_uses_process_group(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 123

        def kill(self):
            calls.append(("kill",))

    monkeypatch.setattr(process.os, "getpgid", lambda pid: 456)
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda pgid, sig: calls.append(("killpg", pgid, sig)),
    )

    process.terminate_owned_process(FakeProcess())

    assert calls == [("killpg", 456, signal.SIGKILL)]


def test_owned_process_is_killed_when_communicate_fails(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 123

        def communicate(self, timeout=None):
            raise UnicodeDecodeError("utf-8", b"\\xff", 0, 1, "invalid")

        def wait(self):
            calls.append(("wait",))

        def kill(self):
            calls.append(("kill",))

    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(process.os, "getpgid", lambda pid: 456)
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda pgid, sig: calls.append(("killpg", pgid, sig)),
    )

    with pytest.raises(UnicodeDecodeError):
        process.run_owned(["ffmpeg"], capture_output=True, text=True)

    assert ("killpg", 456, signal.SIGKILL) in calls
    assert ("wait",) in calls


def test_owned_process_kills_process_group_on_keyboard_interrupt(monkeypatch):
    calls = []

    class FakeProcess:
        pid = 123
        returncode = -signal.SIGKILL

        def communicate(self, timeout=None):
            calls.append(("communicate", timeout))
            if timeout is not None:
                raise KeyboardInterrupt
            return "", ""

    fake = FakeProcess()
    monkeypatch.setattr(process.subprocess, "Popen", lambda *args, **kwargs: fake)
    monkeypatch.setattr(process.os, "getpgid", lambda pid: 456)
    monkeypatch.setattr(
        process.os,
        "killpg",
        lambda pgid, sig: calls.append(("killpg", pgid, sig)),
    )

    with pytest.raises(KeyboardInterrupt):
        process.run_owned(["ffmpeg"], timeout=3.0)

    assert ("killpg", 456, signal.SIGKILL) in calls
    assert ("communicate", None) in calls


def test_none_passes_original_audio_or_drops_it():
    stmts, target = _audio_graph(_req("none"), has_audio=True, has_music=False)
    assert (stmts, target) == ([], "0:a")
    stmts, target = _audio_graph(_req("none"), has_audio=False, has_music=False)
    assert (stmts, target) == ([], None)


def test_job_child_contains_traversal_and_absolute_paths(tmp_path):
    job = tmp_path / "job"
    for evil in ("../other/music.mp3", "/etc/passwd", "sub/dir/x.mp3"):
        resolved = _job_child(job, evil)
        assert resolved.parent == job  # stays directly inside the job dir
    assert _job_child(job, "music.m4a").name == "music.m4a"
