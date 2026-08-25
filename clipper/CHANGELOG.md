# Changelog

All notable changes to RiceClipper are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **v1 vertical slice — initial implementation.** End-to-end pipeline from the
  ratified design (SPEC.md D1–D13):
  - `transcribe/` — faster-whisper wrapper (`whisper.py`, env-tunable model) and
    pure word→phrase chunking (`phrasing.py`).
  - `render/` — parameterised ASS builder with word-highlight captions + top
    header plate (`ass.py`, `StyleConfig`), 9:16 passthrough vs blur-pad
    geometry (`geometry.py`), and single-`filter_complex` ffmpeg orchestration
    with audio replace/mix-under (`pipeline.py`).
  - `app/` — local FastAPI server, in-memory job store, ffprobe + libass
    capability checks, Pydantic contracts.
  - `web/` — vanilla review-gate UI: upload → edit transcript / header / music →
    render → download.
  - `tests/` — unit tests over phrasing, ASS generation, and emoji detection.
  - Pinned `requirements.txt`; added `requirements-dev.txt`.
- **Color-emoji headers (spike PASSED).** libass can't burn color emoji on the
  macOS/CoreText toolchain, so headers containing emoji are rendered to a
  transparent PNG (`render/header_image.py`, Pillow + Apple Color Emoji) and
  composited via ffmpeg `overlay`; text-only headers stay on the libass path.
  Degrades to the libass header if image rendering fails.

### Fixed
- **Rendered-clip playback.** Output audio is resampled to **48 kHz** — 44.1 kHz
  content against a 48 kHz macOS output device triggered Chrome
  `AUDIO_RENDERER_ERROR`. (Root cause of the remaining no-sound case was a local
  audio-stack/virtual-HAL issue, not the files.)
- **Review UI video preview** now plays from client-side blobs (fresh `<video>`
  element per file), and transcription is a separate request so the upload no
  longer holds the client File during playback. Source preview starts muted;
  audio review is on the rendered output. "Start over" now stops playback.

### Requirements
- ffmpeg must be **built with libass** (`subtitles` filter). Homebrew's stock
  formula omits it — use `brew install homebrew-ffmpeg/ffmpeg/ffmpeg`.
- Color-emoji headers need a Pillow-renderable color-emoji font (Apple Color
  Emoji on macOS; the Homebrew Noto build rasterizes blank).
