# RiceClipper

Turns short (**under ~1 minute**) vertical videos into post-ready clips with
**word-synced burned-in captions** and an **on-screen header**.

It is the render chassis for a larger clipping concept ("Path 3"): no clip
*selection* intelligence yet — just clean captioning, header, and vertical export.
It is a standalone project, distinct from **RicePoster** (the posting harness),
built to an output contract that lets its clips drop into RicePoster later.

> **Status: design ratified, pre-implementation.** The design is locked in
> [`SPEC.md`](./SPEC.md). No application code is written yet. The setup/run
> sections below describe the *intended* v1 and are not runnable until the build
> phase is authorized.

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
- **ffmpeg** available on `PATH` (must include libass; a color-emoji font is
  needed for emoji headers — see the open spike below)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# faster-whisper downloads its model on first run
```

## Run (forthcoming)

Not yet implemented. The intended entry point is a local FastAPI server hosting
the review UI at `localhost:8000`, mirroring RicePoster's setup.

## Repo layout

```
app/              FastAPI app — server + render orchestration endpoints
transcribe/       faster-whisper wrapper → word-level caption lines
render/           ffmpeg + ASS rendering (captions, header, audio mix)
  templates/      ASS caption/header templates
web/              static HTML/JS review UI
outputs/          rendered clips (gitignored)
docs/
  spikes/         de-risking investigations (see emoji-burn-in)
  adr/            architecture decision records (optional, future)
SPEC.md           v1 design — source of truth
ROADMAP.md        v1 → Wave 1 → Wave 2 → deferred
CLAUDE.md         operating context for AI agent sessions
CHANGELOG.md      release history
```

## Open item before Wave 1

**Color-emoji burn-in** must be verified — libass can render color emoji as
monochrome or empty boxes without the right font. It's header-critical and blocks
the auto-header. See [`docs/spikes/emoji-burn-in.md`](./docs/spikes/emoji-burn-in.md).

## Docs

- [`SPEC.md`](./SPEC.md) — the ratified v1 design and decision log
- [`ROADMAP.md`](./ROADMAP.md) — what's after v1 and in what order
- [`CLAUDE.md`](./CLAUDE.md) — rules and context for agent sessions
