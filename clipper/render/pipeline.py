"""Render orchestration: ASS + geometry + audio → 1080x1920 H.264/AAC mp4.

Builds a single ffmpeg ``filter_complex`` invocation covering SPEC.md §4 steps
5-8: normalize source coordinates, apply crop/pass/blur-pad geometry, burn the
caption ASS via libass, overlay the Pillow header PNG, mix audio, and encode.
Caption timing is already baked to the timeline in seconds, so mixing music
here cannot affect sync (SPEC.md §4 step 7).

ffmpeg runs with ``cwd`` set to the job dir and the ASS referenced by bare
filename, which sidesteps the notoriously fragile ``subtitles`` path escaping.
"""

from __future__ import annotations

import logging
import math
import os
from pathlib import Path

from app.models import CropPlan, RenderRequest
from app.probe import MediaInfo
from app.process import ProcessTimeoutError, run_owned
from render import geometry
from render.ass import StyleConfig, apply_header_look, build_ass, style_for_presets
from render.header_image import render_header_png
from render.text_image import DEFAULT_FONT, FONT_CHOICES

logger = logging.getLogger("riceclipper")

ASS_NAME = "captions.ass"
HEADER_PNG = "header.png"
OUTPUT_NAME = "output.mp4"
PHOTO_FPS = 30
MUSIC_FADE_IN_S = 0.5
MUSIC_FADE_OUT_S = 1.0


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


def _music_fades(start: float, duration: float | None) -> tuple[str, str]:
    """Return the (fade-in, fade-out) filters for added music (Issue #55).

    A segment that starts past 0 fades in over 0.5 s, so it does not cut in
    mid-note. Every added track fades out over the last 1 s of the clip. Each
    fade is at most half the clip, so on a very short clip they cannot overlap.
    """
    if duration is None:
        return "", ""
    fade_in = min(MUSIC_FADE_IN_S, duration / 2)
    fade_out = min(MUSIC_FADE_OUT_S, duration / 2)
    in_filter = f",afade=t=in:st=0:d={fade_in:g}" if start > 0 else ""
    out_start = _duration_arg(duration - fade_out)
    out_filter = f",afade=t=out:st={out_start}:d={fade_out:g}"
    return in_filter, out_filter


def _audio_graph(
    req: RenderRequest,
    has_audio: bool,
    has_music: bool,
    duration: float | None = None,
):
    """Return (statements, map_target). map_target is None for no audio.

    When ``duration`` is supplied, every audio branch is padded and explicitly
    trimmed to the probed video duration. This keeps a short source or music
    stream from deciding the output length. Added music also fades in and out
    (see ``_music_fades``); the original audio never fades.
    """
    mode = req.music.mode if has_music else "none"
    vol = req.music.volume
    duration_filter = ""
    if duration is not None:
        duration_filter = f",atrim=duration={_duration_arg(duration)}"
    fade_in, fade_out = _music_fades(req.music.start, duration)
    music = f"[1:a]volume={vol}{fade_in},apad{fade_out}{duration_filter}"

    if mode == "replace":
        return [f"{music}[aout]"], "[aout]"
    if mode == "mix" and has_audio:
        return (
            [
                f"[0:a]apad{duration_filter}[orig]",
                f"{music}[m]",
                "[orig][m]amix=inputs=2:duration=first:normalize=0"
                f"{duration_filter}[aout]",
            ],
            "[aout]",
        )
    if mode == "mix":  # music but original is silent
        return [f"{music}[aout]"], "[aout]"
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
    still: bool = False,
    music_start: float = 0.0,
) -> list[str]:
    """Build the ffmpeg command independently of process execution."""
    cmd = ["ffmpeg", "-y"]
    if still:
        # A still photo (Issue #54) loops as a constant-rate video input; the
        # output ``-t`` below bounds its length.
        cmd += ["-loop", "1", "-framerate", str(PHOTO_FPS)]
    cmd += ["-i", str(source_path)]
    if music_path is not None:
        if music_start > 0:
            # Input seek: the music segment starts here and its timestamps
            # restart at 0, so fades and trims count from the segment start.
            cmd += ["-ss", _duration_arg(music_start)]
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


def style_for_request(req) -> StyleConfig:
    """The ``StyleConfig`` a render or preview request asks for.

    The caption preset and the header preset fill it in; ``header_look``, when
    sent, then sets every header control.
    """
    style = style_for_presets(
        getattr(req, "caption_style", "classic"), req.header_style
    )
    if req.header_look is not None:
        style = apply_header_look(style, req.header_look)
    return style


def render(
    job_dir: str | Path,
    source_path: str | Path,
    info: MediaInfo,
    req: RenderRequest,
    style: StyleConfig | None = None,
    plan: CropPlan | None = None,
    notes: list[str] | None = None,
) -> Path:
    """Render one clip; returns the output mp4 path. Raises RenderError.

    ``plan`` is a resolved :class:`CropPlan`. When its ``decision`` is ``crop``
    the video uses the subject-crop path (a moving 9:16 window) instead of
    blur-pad. The caller resolves the plan (ADR-001, F4). ``None`` keeps the
    blur-pad / pass-through behaviour.

    If the header PNG cannot be drawn, the header falls back to a minimal
    libass text line and the reason is appended to ``notes``.
    """
    job_dir = Path(job_dir)
    source_path = Path(source_path)
    if not math.isfinite(info.duration) or info.duration <= 0:
        raise RenderError(f"invalid video duration: {info.duration!r}")

    # 1. Header. Pillow draws every non-empty header, with or without emoji,
    # to a full-frame PNG that is overlaid after the captions (RiceSuite #65).
    # If that fails, a minimal libass text header keeps the header on the clip
    # and the reason goes back to the caller, so the fallback is not silent.
    style = style or style_for_request(req)
    header = req.header.strip()
    overlay_header = bool(header)
    fallback_header = ""
    if overlay_header:
        try:
            render_header_png(
                header,
                job_dir / HEADER_PNG,
                style,
                canvas=(geometry.TARGET_W, geometry.TARGET_H),
            )
        except Exception as exc:
            logger.warning("header PNG failed; using the libass header: %s", exc)
            overlay_header = False
            fallback_header = header
            if notes is not None:
                notes.append(f"The header used the basic text renderer: {exc}")

    family = FONT_CHOICES.get(style.header_font, FONT_CHOICES[DEFAULT_FONT])
    ass_text = build_ass(
        req.words,
        captions_on=req.captions_on,
        duration=info.duration,
        style=style,
        fallback_header=fallback_header,
        fallback_family=family.ass_family,
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
    # ffmpeg applies display rotation before the filter graph. Normalize that
    # result (including non-square sample aspect ratios) to the same square-pixel
    # dimensions used by probing, detection, and framing.
    video_stmts = [
        geometry.normalize_statement(info.width, info.height, "[0:v]", "[src]")
    ]
    if geometry.is_target(info.width, info.height):
        # A 1080x1920 job passes through, whatever the plan says. Detection only
        # runs on landscape input, so a vertical job never crops (ADR-001).
        video_stmts.append(f"[src]subtitles={ASS_NAME}{sub_out}")
    elif plan is not None and plan.decision == "crop":
        # Subject crop: write the sendcmd command file, then drive a moving 9:16
        # window over the source.
        (job_dir / geometry.CROP_CMD_NAME).write_text(
            geometry.crop_command_file(plan), encoding="utf-8"
        )
        video_stmts.extend(geometry.crop_statements(plan, "[src]", "[base]"))
        video_stmts.append(f"[base]subtitles={ASS_NAME}{sub_out}")
    else:
        video_stmts.extend(geometry.blur_pad_statements("[src]", "[base]"))
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
        still=info.still,
        music_start=req.music.start,
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
