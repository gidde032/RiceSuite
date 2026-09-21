# RiceClipper

Turns short (**under ~1 minute**) vertical or landscape videos into post-ready clips with
**word-synced burned-in captions** and an **on-screen header**.

It is the render chassis for a larger clipping concept ("Path 3"): no clip
*selection* intelligence yet — just clean captioning, header, and vertical export.
It is a standalone project, distinct from **RicePoster** (the posting harness),
with an implemented local-filesystem handoff that RicePoster can pull from.

> **Status: v1 slice implemented; hardening, bounded visual presets, Slate UI,
> fixed lyric-caption presets, and Wave-1 auto-header complete.** End-to-end rendering and shutdown
> cleanup are verified with a libass-enabled ffmpeg. The design is recorded in
> [`SPEC.md`](./SPEC.md). Burn-in requires an ffmpeg with libass (see setup) —
> the stock Homebrew formula omits it.

## What it does (v1)

Upload one or more clips → auto-transcribe with word-level timing → review and
edit the transcript and header → burn in captions + header → export
**1080×1920 H.264**. Landscape input uses local face detection to choose a
moving single-subject crop, with blur-pad as the automatic fallback and an
explicit per-clip choice. The crop uses a stabilized strong lock: small face
movements do not move the window, ordinary corrections move smoothly, and
scene/target reacquisition cuts still snap immediately. Non-9:16 vertical input
is blur-padded. Optional
added music can replace or mix under the original audio.

Full scope, deferred roadmap, and the decision log are in [`SPEC.md`](./SPEC.md).

## Boundary

RiceClipper **does not post, publish, or upload content anywhere.** It reads
local files and writes local files. Posting — and its approval gate — belongs to
RicePoster, which separately pulls from RiceClipper's local handoff. See
`SPEC.md` §3.

The **only** outbound network feature is the optional on-screen header
generator, and it is **opt-in**: nothing is sent after transcription
automatically. It requires an `ANTHROPIC_API_KEY` and runs only when you click
**✨ Generate** in the review UI. Everything else — transcription, subject
detection, rendering — runs fully offline. See [`SECURITY.md`](./SECURITY.md)
for exactly what is transmitted, and [`.env.example`](./.env.example) for
configuration.

## Stack

- Python + FastAPI, served locally
- Vanilla HTML/JS review UI
- **faster-whisper** for word-level transcription
- **OpenCV YuNet** for local landscape subject detection
- **ffmpeg + libass** (ASS subtitles) for caption/header burn-in and audio mix
- Anthropic Sonnet for the **opt-in** on-screen header generator (Wave 1) —
  the only outbound network feature; off unless you set a key and click Generate

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

Open `localhost:8000`, choose one or more clips, edit the transcript / header /
landscape geometry, optionally add music, then render and download. Failed
renders remain in the review queue so settings can be changed and **Render all**
can be tried again. `GET /api/health`
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
pytest -m smoke -q                     # the 8-test fast tier
pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85

# Maintainer-only subject-crop fixture gate (six named roles + sheet review)
python scripts/crop_check.py fixtures/landscape --contact-sheets-approved
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
  adr/            accepted architecture decision records and amendments
SPEC.md           v1 design — source of truth
ROADMAP.md        v1 → Wave 1 → Wave 2 → deferred
CLAUDE.md         operating context for AI agent sessions
CHANGELOG.md      release history
```

## Current implementation notes

Color-emoji burn-in is resolved through the PNG-overlay fallback documented in
[`docs/spikes/emoji-burn-in.md`](./docs/spikes/emoji-burn-in.md). The Wave-1
header generator is implemented as an opt-in action (explicit button, key-gated)
with manual entry as the always-available fallback. Landscape subject tracking now
uses the universal Level-5 motion policy for speech and music: a 20% outer hold
zone, a 10% inner settle boundary, and 30 Hz interpolation for ordinary motion;
confirmed cuts, inferred face jumps, and returns after track loss remain
immediate. See the
[`subject-crop design spec`](./docs/design/subject-crop-spec.md). The v1
hardening pass also adds short-music replace-mode duration protection,
bounded Whisper/ffmpeg threading, owned subprocess cleanup, manual media-cache
clearing, API error-state cleanup, and semaphore-free transcription shutdown.

## Docs

- [`SPEC.md`](./SPEC.md) — the ratified v1 design and decision log
- [`ROADMAP.md`](./ROADMAP.md) — what's after v1 and in what order
- [`SECURITY.md`](./SECURITY.md) — privacy, secrets, and what leaves your machine
- [`CLAUDE.md`](./CLAUDE.md) — rules and context for agent sessions

## License

Released under the [MIT License](./LICENSE).
