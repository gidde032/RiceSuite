"""Render orchestration: ASS + geometry + audio → 1080x1920 H.264/AAC mp4.

Builds a single ffmpeg ``filter_complex`` invocation covering SPEC.md §4 steps
5-8: blur-pad geometry, burn the caption+header ASS via libass, mix audio, and
encode. Caption timing is already baked to the timeline in seconds, so mixing
music here cannot affect sync (SPEC.md §4 step 7).

ffmpeg runs with ``cwd`` set to the job dir and the ASS referenced by bare
filename, which sidesteps the notoriously fragile ``subtitles`` path escaping.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from app.models import RenderRequest
from app.probe import MediaInfo
from render import geometry
from render.ass import StyleConfig, build_ass
from render.header_image import has_emoji, render_header_png

ASS_NAME = "captions.ass"
HEADER_PNG = "header.png"
OUTPUT_NAME = "output.mp4"


class RenderError(RuntimeError):
    pass


def _audio_graph(req: RenderRequest, has_audio: bool, has_music: bool):
    """Return (statements, map_target). map_target is None for no audio.

    Music-only branches are `apad`-ed so a music track shorter than the video
    doesn't truncate the clip via `-shortest` — `-shortest` then bounds the output
    to the (finite) video stream. `mix` with original audio is already bounded by
    `amix=duration=first`.
    """
    mode = req.music.mode if has_music else "none"
    vol = req.music.volume

    if mode == "replace":
        return [f"[1:a]volume={vol},apad[aout]"], "[aout]"
    if mode == "mix" and has_audio:
        return (
            [f"[1:a]volume={vol}[m]", "[0:a][m]amix=inputs=2:duration=first:normalize=0[aout]"],
            "[aout]",
        )
    if mode == "mix":  # music but original is silent
        return [f"[1:a]volume={vol},apad[aout]"], "[aout]"
    # none
    return [], ("0:a" if has_audio else None)


def _job_child(job_dir: Path, filename: str) -> Path:
    """Resolve a client-supplied filename to a path INSIDE job_dir (basename only).

    Prevents a crafted `/render` request from pointing at another job's dir or an
    arbitrary path via `..` or an absolute path.
    """
    return Path(job_dir) / Path(filename).name


def render(
    job_dir: str | Path,
    source_path: str | Path,
    info: MediaInfo,
    req: RenderRequest,
    style: StyleConfig | None = None,
) -> Path:
    """Render one clip; returns the output mp4 path. Raises RenderError."""
    job_dir = Path(job_dir)
    source_path = Path(source_path)

    # 1. Header routing. libass can't render color emoji on this toolchain, so a
    # header containing emoji is drawn to a PNG and composited via `overlay`; the
    # ASS then omits the header. Text-only headers stay on the libass path. If the
    # image render fails for any reason, degrade to the libass header rather than
    # failing the whole render (text shows; emoji may box).
    style = style or StyleConfig()
    overlay_header = bool(req.header.strip()) and has_emoji(req.header)
    if overlay_header:
        try:
            render_header_png(
                req.header, job_dir / HEADER_PNG, style,
                canvas=(geometry.TARGET_W, geometry.TARGET_H),
            )
        except Exception:  # noqa: BLE001 - degrade to libass header
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
    audio_stmts, audio_map = _audio_graph(req, info.has_audio, has_music)

    # 3. Video graph: blur-pad (if needed) → burn subtitles → optional header overlay.
    sub_out = "[subbed]" if overlay_header else "[vout]"
    if geometry.is_target(info.width, info.height):
        video_stmts = [f"[0:v]subtitles={ASS_NAME}{sub_out}"]
    else:
        video_stmts = geometry.blur_pad_statements("[0:v]", "[base]")
        video_stmts.append(f"[base]subtitles={ASS_NAME}{sub_out}")

    if overlay_header:
        header_input = 1 + (1 if has_music else 0)
        video_stmts.append(f"[subbed][{header_input}:v]overlay=0:0[vout]")

    filter_complex = ";".join(video_stmts + audio_stmts)

    # 4. Assemble and run ffmpeg.
    cmd = ["ffmpeg", "-y", "-i", str(source_path)]
    if has_music:
        cmd += ["-i", str(music_path)]
    if overlay_header:
        cmd += ["-i", HEADER_PNG]
    cmd += ["-filter_complex", filter_complex, "-map", "[vout]"]
    if audio_map is not None:
        cmd += ["-map", audio_map]
    else:
        cmd += ["-an"]
    cmd += [
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
    ]
    if audio_map is not None:
        # Resample audio to 48 kHz stereo. Many macOS audio output devices run at
        # 48 kHz, and Chrome throws an "audio render error" on 44.1 kHz content
        # against a 48 kHz device (phone/social sources are usually 44.1 kHz).
        # 48 kHz is also the standard rate for video deliverables.
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    cmd += ["-movflags", "+faststart", "-shortest", OUTPUT_NAME]

    proc = subprocess.run(cmd, cwd=str(job_dir), capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join(proc.stderr.strip().splitlines()[-15:])
        raise RenderError(f"ffmpeg failed:\n{tail}")

    return job_dir / OUTPUT_NAME
