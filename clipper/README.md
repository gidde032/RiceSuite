# RiceClipper

Turns short (**under ~1 minute**) vertical videos into post-ready clips with
**word-synced burned-in captions** and an **on-screen header**.

It is the render chassis for a larger clipping concept ("Path 3"): no clip
*selection* intelligence yet — just clean captioning, header, and vertical export.
It is a standalone project, distinct from **RicePoster** (the posting harness),
built to an output contract that lets its clips drop into RicePoster later.

> **Status: v1 slice implemented; end-to-end render verified with a libass-enabled ffmpeg.**
> The design is locked in [`SPEC.md`](./SPEC.md). The full v1 vertical slice is
> built and the pure-Python core is unit-tested. Burn-in requires an ffmpeg with
> libass (see setup) — the stock Homebrew formula omits it.

## What it does (v1)

Upload a vertical clip → auto-transcribe with word-level timing → review and edit
the transcript and type a header → burn in captions + header → export
**1080×1920 H.264**. Non-9:16 vertical inputs are blur-padded (never cropped).
Optional added-music track can replace or mix under the original audio.

Full scope, deferred roadmap, and the decision log are in [`SPEC.md`](./SPEC.md).

## Boundary

RiceClipper **does not post, publish, or upload content anywhere.** It reads
local files and writes local files. Posting — and its approval gate — belongs to
RicePoster, at the future integration point. See `SPEC.md` §3.

## Stack

- Python + FastAPI, served locally
- Vanilla HTML/JS review UI
- **faster-whisper** for word-level transcription
- **ffmpeg + libass** (ASS subtitles) for caption/header burn-in and audio mix
- Anthropic Sonnet for the *deferred* auto-header (Wave 1)

## Setup (intended)

Prerequisites:

- Python 3.11+
- **ffmpeg with libass** on `PATH`. The stock Homebrew `ffmpeg` formula does
  **not** include libass (no `subtitles` filter). Install the libass-enabled tap
  build (unlink core first so the binary doesn't conflict):
  ```bash
  brew unlink ffmpeg
  brew install homebrew-ffmpeg/ffmpeg/ffmpeg   # builds from source (~10-20 min)
  ```
  Verify: `ffmpeg -hide_banner -filters | grep -w subtitles`.
- **Color emoji in headers** works out of the box on macOS. libass can't burn
  color emoji, so headers containing emoji are rendered to an image (Pillow +
  Apple Color Emoji, built into macOS) and composited via ffmpeg `overlay`;
  text-only headers use libass directly. See `docs/spikes/emoji-burn-in.md`.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt        # add -dev variant for tests
# faster-whisper downloads its model on first run
```

## Run

```bash
uvicorn app.main:app --reload          # serves the review UI at localhost:8000
```

Open `localhost:8000`, upload a vertical clip, edit the transcript / type a
header / (optionally) add music, then render and download. `GET /api/health`
reports whether ffmpeg + libass are present.

Rendered sources and intermediate files remain in the local `.riceclipper_work/`
cache until you explicitly clear them with the **Clear media cache** button in
the UI. Clearing is disabled while a transcription or render is active; it
does not remove the original files selected in your browser or the Whisper
model cache. For a gentle default on an 8-core machine, transcription and
encoding use four worker threads. Override them when needed with, for example:

```bash
export RICECLIPPER_WHISPER_CPU_THREADS=4
export RICECLIPPER_FFMPEG_THREADS=4
```

The Whisper tokenizer safeguard can also be made explicit in the shell before
launching the server:

```bash
export TOKENIZERS_PARALLELISM=false
```

## Test

```bash
pip install -r requirements-dev.txt
pytest -q                              # pure-Python core; no ffmpeg needed
```

## Repo layout

```
app/              FastAPI app — server + render orchestration endpoints
transcribe/       faster-whisper wrapper → word-level caption lines
render/           ffmpeg + ASS rendering (captions, header, audio mix)
  templates/      ASS caption/header templates
web/              static HTML/JS review UI
tests/            unit tests (phrasing + ASS generation)
.riceclipper_work/ app-owned uploaded sources, intermediates, and outputs (gitignored)
docs/
  spikes/         de-risking investigations (see emoji-burn-in)
  adr/            architecture decision records (optional, future)
SPEC.md           v1 design — source of truth
ROADMAP.md        v1 → Wave 1 → Wave 2 → deferred
CLAUDE.md         operating context for AI agent sessions
CHANGELOG.md      release history
```

## Current implementation notes

Color-emoji burn-in is resolved through the PNG-overlay fallback documented in
[`docs/spikes/emoji-burn-in.md`](./docs/spikes/emoji-burn-in.md). The Wave-1
auto-header remains deferred roadmap scope, rather than an unresolved v1 block.

## Docs

- [`SPEC.md`](./SPEC.md) — the ratified v1 design and decision log
- [`ROADMAP.md`](./ROADMAP.md) — what's after v1 and in what order
- [`CLAUDE.md`](./CLAUDE.md) — rules and context for agent sessions
