from pathlib import Path

from app.models import MusicSettings, RenderRequest
from render.pipeline import _audio_graph, _job_child


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
