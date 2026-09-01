# RiceClipper

Turns short (**under ~1 minute**) vertical videos into post-ready clips with
**word-synced burned-in captions** and an **on-screen header**.

It is the render chassis for a larger clipping concept ("Path 3"): no clip
*selection* intelligence yet — just clean captioning, header, and vertical export.
It is a standalone project, distinct from **RicePoster** (the posting harness),
with an implemented local-filesystem handoff that RicePoster can pull from.

> **Status: v1 slice implemented; hardening, bounded visual presets, Slate UI,
> and fixed lyric-caption presets complete.** End-to-end rendering and shutdown
> cleanup are verified with a libass-enabled ffmpeg. The design is recorded in
> [`SPEC.md`](./SPEC.md). Burn-in requires an ffmpeg with libass (see setup) —
> the stock Homebrew formula omits it.

## What it does (v1)

Upload a vertical clip → auto-transcribe with word-level timing → review and edit
the transcript and type a header → burn in captions + header → export
**1080×1920 H.264**. Non-9:16 vertical inputs are blur-padded (never cropped).
Optional added-music track can replace or mix under the original audio.

Full scope, deferred roadmap, and the decision log are in [`SPEC.md`](./SPEC.md).

## Boundary

RiceClipper **does not post, publish, or upload content anywhere.** It reads
local files and writes local files. Posting — and its approval gate — belongs to
RicePoster, which separately pulls from RiceClipper's local handoff. See
`SPEC.md` §3.

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

## Test and quality gates

```bash
pip install -r requirements-dev.txt
pytest -q                              # pure-Python core; no ffmpeg needed

ruff check . && ruff format --check .  # lint + format (matches CI)
pytest -m smoke -q                     # the 6-test fast tier
pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85
```

CI (`.github/workflows/ci.yml`) runs the same ruff checks and the full suite
with an **85% coverage floor** on every PR and push to `main`. `tests/test_gates.py`
locks those numbers so they can't silently drift. Optional local hooks mirror CI:

```bash
pre-commit install --hook-type pre-commit --hook-type pre-push
```

The commit tier runs ruff + the smoke tier; the push tier runs the full suite
and coverage floor. A `main` branch-protection ruleset is prepared in
`.github/rulesets/` but not yet applied (rulesets need GitHub Pro on a private
repo).

## Repo layout

```
app/              FastAPI app — server + render orchestration endpoints
transcribe/       faster-whisper wrapper → word-level caption lines
render/           ffmpeg + ASS rendering (captions, header, audio mix)
  templates/      ASS caption/header templates
web/              static HTML/JS review UI
tests/            unit tests + test_gates.py (quality-gate meta-tests)
.riceclipper_work/ app-owned uploaded sources, intermediates, and outputs (gitignored)
.github/          CI workflow, issue/PR templates, pending branch ruleset
pyproject.toml    ruff + pytest configuration
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
The v1 hardening pass also adds short-music replace-mode duration protection,
bounded Whisper/ffmpeg threading, owned subprocess cleanup, manual media-cache
clearing, API error-state cleanup, and semaphore-free transcription shutdown.

## Docs

- [`SPEC.md`](./SPEC.md) — the ratified v1 design and decision log
- [`ROADMAP.md`](./ROADMAP.md) — what's after v1 and in what order
- [`CLAUDE.md`](./CLAUDE.md) — rules and context for agent sessions
