"""Render orchestration: ASS + geometry + audio → 1080x1920 H.264/AAC mp4.

Builds a single ffmpeg ``filter_complex`` invocation covering SPEC.md §4 steps
5-8: blur-pad geometry, burn the caption+header ASS via libass, mix audio, and
encode. Caption timing is already baked to the timeline in seconds, so mixing
music here cannot affect sync (SPEC.md §4 step 7).

ffmpeg runs with ``cwd`` set to the job dir and the ASS referenced by bare
filename, which sidesteps the notoriously fragile ``subtitles`` path escaping.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

from app.models import CropPlan, RenderRequest
from app.probe import MediaInfo
from app.process import ProcessTimeoutError, run_owned
from render import geometry
from render.ass import StyleConfig, build_ass, style_for_presets
from render.header_image import has_emoji, render_header_png

ASS_NAME = "captions.ass"
HEADER_PNG = "header.png"
OUTPUT_NAME = "output.mp4"


class RenderError(RuntimeError):
    pass


def _encode_threads() -> int:
    """Return the conservative ffmpeg thread cap, with an environment override."""
    default = max(1, (os.cpu_count() or 1) // 2)
    override = os.environ.get("RICECLIPPER_FFMPEG_THREADS")
    if override is None:
        return default

    try:
        configured = int(override)
    except (TypeError, ValueError):
        return default
    return configured if configured > 0 else default


def _render_timeout(duration: float) -> float:
    """Allow at least two minutes and otherwise ten times the video duration."""
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError(f"invalid media duration: {duration!r}")
    return max(120.0, duration * 10.0)


def _duration_arg(duration: float) -> str:
    """Format a positive media duration for ffmpeg without needless rounding."""
    return str(float(duration))


def _audio_graph(
    req: RenderRequest,
    has_audio: bool,
    has_music: bool,
    duration: float | None = None,
):
    """Return (statements, map_target). map_target is None for no audio.

    When ``duration`` is supplied, every audio branch is padded and explicitly
    trimmed to the probed video duration. This keeps a short source or music
    stream from deciding the output length.
    """
    mode = req.music.mode if has_music else "none"
    vol = req.music.volume
    duration_filter = ""
    if duration is not None:
        duration_filter = f",atrim=duration={_duration_arg(duration)}"

    if mode == "replace":
        return [f"[1:a]volume={vol},apad{duration_filter}[aout]"], "[aout]"
    if mode == "mix" and has_audio:
        return (
            [
                f"[0:a]apad{duration_filter}[orig]",
                f"[1:a]volume={vol},apad{duration_filter}[m]",
                "[orig][m]amix=inputs=2:duration=first:normalize=0"
                f"{duration_filter}[aout]",
            ],
            "[aout]",
        )
    if mode == "mix":  # music but original is silent
        return [f"[1:a]volume={vol},apad{duration_filter}[aout]"], "[aout]"
    # none
    if has_audio and duration is not None:
        return [f"[0:a]apad{duration_filter}[aout]"], "[aout]"
    return [], ("0:a" if has_audio else None)


def _job_child(job_dir: Path, filename: str) -> Path:
    """Resolve a client-supplied filename to a path INSIDE job_dir (basename only).

    Prevents a crafted `/render` request from pointing at another job's dir or an
    arbitrary path via `..` or an absolute path.
    """
    return Path(job_dir) / Path(filename).name


def _ffmpeg_command(
    source_path: Path,
    music_path: Path | None,
    overlay_header: bool,
    filter_complex: str,
    audio_map: str | None,
    duration: float,
) -> list[str]:
    """Build the ffmpeg command independently of process execution."""
    cmd = ["ffmpeg", "-y", "-i", str(source_path)]
    if music_path is not None:
        cmd += ["-i", str(music_path)]
    if overlay_header:
        cmd += ["-i", HEADER_PNG]
    filter_threads = _encode_threads()
    cmd += [
        "-filter_threads",
        str(filter_threads),
        "-filter_complex_threads",
        str(filter_threads),
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
    ]
    if audio_map is not None:
        cmd += ["-map", audio_map]
    else:
        cmd += ["-an"]
    cmd += [
        "-threads",
        str(_encode_threads()),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
    ]
    if audio_map is not None:
        # Resample audio to 48 kHz stereo. Many macOS audio output devices run at
        # 48 kHz, and Chrome throws an "audio render error" on 44.1 kHz content
        # against a 48 kHz device (phone/social sources are usually 44.1 kHz).
        # 48 kHz is also the standard rate for video deliverables.
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    # This is an explicit output bound. Audio filters also trim to this same
    # duration, so output length does not depend on whichever input ends first.
    cmd += [
        "-t",
        _duration_arg(duration),
        "-shortest",
        "-movflags",
        "+faststart",
        OUTPUT_NAME,
    ]
    return cmd


def render(
    job_dir: str | Path,
    source_path: str | Path,
    info: MediaInfo,
    req: RenderRequest,
    style: StyleConfig | None = None,
    plan: CropPlan | None = None,
) -> Path:
    """Render one clip; returns the output mp4 path. Raises RenderError.

    ``plan`` is a resolved :class:`CropPlan`. When its ``decision`` is ``crop``
    the video uses the subject-crop path (a moving 9:16 window) instead of
    blur-pad. The caller resolves the plan (ADR-001, F4). ``None`` keeps the
    blur-pad / pass-through behaviour.
    """
    job_dir = Path(job_dir)
    source_path = Path(source_path)
    if not math.isfinite(info.duration) or info.duration <= 0:
        raise RenderError(f"invalid video duration: {info.duration!r}")

    # 1. Header routing. libass can't render color emoji on this toolchain, so a
    # header containing emoji is drawn to a PNG and composited via `overlay`; the
    # ASS then omits the header. Text-only headers stay on the libass path. If the
    # image render fails for any reason, degrade to the libass header rather than
    # failing the whole render (text shows; emoji may box).
    style = style or style_for_presets(req.caption_style, req.header_style)
    overlay_header = bool(req.header.strip()) and has_emoji(req.header)
    if overlay_header:
        try:
            render_header_png(
                req.header,
                job_dir / HEADER_PNG,
                style,
                canvas=(geometry.TARGET_W, geometry.TARGET_H),
            )
        except Exception:
            overlay_header = False

    ass_text = build_ass(
        req.words,
        header="" if overlay_header else req.header,
        captions_on=req.captions_on,
        duration=info.duration,
        style=style,
    )
    (job_dir / ASS_NAME).write_text(ass_text, encoding="utf-8")

    # 2. Audio graph (input indices: 0 = source, then music, then header PNG).
    has_music = req.music.mode != "none" and bool(req.music.filename)
    music_path = _job_child(job_dir, req.music.filename) if has_music else None
    if has_music and not music_path.exists():
        raise RenderError(f"music file not found: {req.music.filename}")
    audio_stmts, audio_map = _audio_graph(req, info.has_audio, has_music, info.duration)

    # 3. Video graph: crop / blur-pad / pass-through → burn subtitles → header.
    sub_out = "[subbed]" if overlay_header else "[vout]"
    if plan is not None and plan.decision == "crop":
        # Subject crop: write the sendcmd command file, then drive a moving 9:16
        # window over the source. Detection only runs on landscape input, so a
        # crop plan never coexists with 1080x1920 pass-through (ADR-001).
        (job_dir / geometry.CROP_CMD_NAME).write_text(
            geometry.crop_command_file(plan), encoding="utf-8"
        )
        video_stmts = geometry.crop_statements(plan, "[0:v]", "[base]")
        video_stmts.append(f"[base]subtitles={ASS_NAME}{sub_out}")
    elif geometry.is_target(info.width, info.height):
        video_stmts = [f"[0:v]subtitles={ASS_NAME}{sub_out}"]
    else:
        video_stmts = geometry.blur_pad_statements("[0:v]", "[base]")
        video_stmts.append(f"[base]subtitles={ASS_NAME}{sub_out}")

    if overlay_header:
        header_input = 1 + (1 if has_music else 0)
        video_stmts.append(f"[subbed][{header_input}:v]overlay=0:0[vout]")

    filter_complex = ";".join(video_stmts + audio_stmts)

    # 4. Assemble and run ffmpeg.
    cmd = _ffmpeg_command(
        source_path,
        music_path if has_music else None,
        overlay_header,
        filter_complex,
        audio_map,
        info.duration,
    )
    try:
        proc = run_owned(
            cmd,
            cwd=job_dir,
            capture_output=True,
            text=True,
            timeout=_render_timeout(info.duration),
        )
    except ProcessTimeoutError as exc:
        raise RenderError(
            f"ffmpeg timed out after {_render_timeout(info.duration):g} seconds"
        ) from exc
    except OSError as exc:
        raise RenderError(f"could not start ffmpeg: {exc}") from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed:\n{tail}")

    return job_dir / OUTPUT_NAME
