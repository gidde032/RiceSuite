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
detection, rendering — runs fully offline once the Whisper model has been
downloaded (see [First run](#first-run)). See [`SECURITY.md`](./SECURITY.md)
for exactly what is transmitted, and [`.env.example`](./.env.example) for
configuration.

## Stack

- Python + FastAPI, served locally
- Vanilla HTML/JS review UI
- **faster-whisper** for word-level transcription
- **OpenCV YuNet** for local landscape subject detection (model vendored in
  `render/models/`, no download)
- **ffmpeg + libass** (ASS subtitles) for caption/header burn-in and audio mix
- Anthropic Sonnet for the **opt-in** on-screen header generator (Wave 1) —
  the only outbound network feature; off unless you set a key and click Generate

## Requirements

| Requirement | Notes |
| --- | --- |
| **Python 3.11 – 3.14** | CI tests **3.12** (required check) and **3.14** (non-required job). The pinned dependencies install from wheels on 3.11–3.14. **3.10 and older will not work:** the code imports `datetime.UTC`, which is new in 3.11. macOS ships `/usr/bin/python3` as 3.9, so use a python.org, Homebrew, or pyenv interpreter. |
| **ffmpeg with libass** on `PATH` | Required only to render. Transcription, the UI, and the test suite run without it. See below. |
| **macOS** (recommended) | This is the only platform verified end to end. Headers that contain **emoji** use Apple Color Emoji, and header text uses macOS system fonts. Elsewhere, text-only headers still work through libass, but an emoji header fails to render (see [Troubleshooting](#troubleshooting)). |
| Disk / network for the first transcription | faster-whisper downloads the Whisper model (`small` by default, roughly 0.5 GB) from Hugging Face the first time you transcribe. |

### ffmpeg with libass

The stock Homebrew `ffmpeg` formula does **not** include libass, so it has no
`subtitles` filter. Install the libass-enabled tap build instead, unlinking
core first so the two binaries don't conflict:

```bash
brew unlink ffmpeg
brew install homebrew-ffmpeg/ffmpeg/ffmpeg   # builds from source (~10-20 min)
```

On Linux, most distribution `ffmpeg` packages already include libass.

Check that the filter is present. This should print a line containing
`subtitles`:

```bash
ffmpeg -hide_banner -filters | grep -w subtitles
```

**Color emoji in headers** works out of the box on macOS. libass can't burn
color emoji, so a header containing emoji is rendered to an image (Pillow +
Apple Color Emoji) and composited with ffmpeg `overlay`. Text-only headers use
libass directly. See `docs/spikes/emoji-burn-in.md`.

## Install

Run these from the repo root. RiceClipper runs in place and is not installed as
a package. Use any Python 3.11–3.14. Check what you have with `python3 --version`. If that
prints 3.9 or 3.10, call a newer binary explicitly, for example `python3.14`
(see `ls /opt/homebrew/bin/python3.* /usr/local/bin/python3.*`):

```bash
python3 -m venv .venv                 # or: python3.14 -m venv .venv
source .venv/bin/activate             # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt        # to run the app
python -m pip install -r requirements-dev.txt    # adds dev tools (includes requirements.txt)
```

`requirements-dev.txt` already includes `requirements.txt` and adds pytest, ruff,
and pre-commit, so contributors only need the second install.

## Configure

Every setting is optional. With nothing configured, RiceClipper runs fully
locally and the header field is manual-only. The variables are documented in
[`.env.example`](./.env.example):

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | *(unset)* | Enables **✨ Generate** for the header. Without it, clicking Generate returns an error and you type the header by hand. |
| `RICECLIPPER_HEADER_STYLE` | `generic-header` | Header prompt style; must match a file in `prompts/`. |
| `RICECLIPPER_HEADER_MODEL` | `claude-sonnet-5` | Anthropic model for header generation. |
| `RICECLIPPER_WHISPER_MODEL` | `small` | `tiny` / `base` / `small` / `medium` / `large-v3`. Smaller is a faster, smaller download. |
| `RICECLIPPER_WHISPER_DEVICE` | `cpu` | `cpu` or `cuda`. |
| `RICECLIPPER_WHISPER_COMPUTE` | `int8` | ctranslate2 compute type. |
| `RICECLIPPER_WHISPER_CPU_THREADS` | half the logical cores | Transcription threads. |
| `RICECLIPPER_FFMPEG_THREADS` | half the logical cores | ffmpeg encode threads. |
| `RICECLIPPER_HANDOFF_DIR` | `~/riceclipper-handoff` | Where **Send to RicePoster** writes batches. |
| `RICECLIPPER_SEARCHER_INBOX` | `~/ricesearcher-handoff` | Where **Pull from RiceSearcher** reads batches. |

**RiceClipper does not read `.env` by itself.** You have two ways to load it.
Pass the file to uvicorn, which can read it because `uvicorn[standard]` ships
`python-dotenv`:

```bash
cp .env.example .env        # then edit .env; it is gitignored
uvicorn app.main:app --env-file .env
```

Or export the variables in your shell before you start the server:

```bash
export RICECLIPPER_WHISPER_MODEL=base
```

The Whisper settings are read once, when the server starts. Restart the server
after you change them.

> **Handoff directory is shared.** If you also run RicePoster, it pulls from
> `RICECLIPPER_HANDOFF_DIR` (default `~/riceclipper-handoff`). When you are
> experimenting, point it somewhere else, for example
> `RICECLIPPER_HANDOFF_DIR=/tmp/rc-handoff`, so test batches don't reach your
> live RicePoster queue.

## First run

```bash
source .venv/bin/activate
uvicorn app.main:app --reload            # or add --env-file .env
```

Open <http://localhost:8000>. uvicorn uses port 8000 by default; pass
`--port 8765` to change it. At startup the server logs a warning if ffmpeg or
libass is missing. You can also check at any time:

```bash
curl -s localhost:8000/api/health        # {"ffmpeg":true,"libass":true}
```

The repo ships no sample media. For a first smoke run, use any short phone clip,
or generate a 5-second landscape test clip with a tone:

```bash
ffmpeg -f lavfi -i testsrc2=size=1280x720:rate=30 -f lavfi -i sine=frequency=440 \
  -t 5 -c:v libx264 -pix_fmt yuv420p -c:a aac -shortest sample.mp4
```

A synthetic clip has no speech, so the transcript comes back empty or nearly
empty. That is expected. Type a caption or header by hand to see the burn-in. There is also no
face in it, so the landscape crop falls back to blur-pad.

**The first transcription is slow.** The first clip triggers the Whisper model
download, which goes to the Hugging Face cache (`~/.cache/huggingface/hub`, or
wherever `HF_HOME` points). Later runs reuse the cache and need no network.
Set `RICECLIPPER_WHISPER_MODEL=tiny` for a quick first try.

## Using it

1. **Add clips.** Choose one or more video files, or click **Pull from
   RiceSearcher** to ingest the oldest batch waiting in
   `RICECLIPPER_SEARCHER_INBOX`. If there is nothing there, it adds nothing.
   Each clip becomes a review card and is transcribed automatically.
2. **Review.** Edit the transcript and choose a caption style. The eleven fixed
   presets include four lyric presets: paste lyrics, then click **Align** to
   time them to the audio, and use **Restore transcript** to go back. Type a
   header, or click **✨ Generate** if `ANTHROPIC_API_KEY` is set, and choose a
   header style. For landscape clips, choose **auto**, **crop**, or
   **blur-pad**.
3. **Music (optional).** Attach an audio file, then choose to *replace* the
   original audio or *mix* the music under it.
4. **Render all.** Each clip is rendered to 1080×1920 H.264/AAC mp4 and can be
   previewed and downloaded. Failed renders stay in the queue, so you can change
   their settings and click **Render all** again.
5. **Send to RicePoster (optional).** This writes the rendered clips plus a
   `manifest.json` as one batch directory under `RICECLIPPER_HANDOFF_DIR`. Only
   local files are written. The contract is in
   [`docs/integration/riceposter-handoff.md`](./docs/integration/riceposter-handoff.md).

Uploaded sources, intermediate files, and renders stay in the local
`.riceclipper_work/` cache until you click **Clear media cache**. Clearing is
disabled while a transcription or render is running. It does not remove your
original files or the Whisper model cache.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Render fails; the startup log says *"This ffmpeg has no libass"*, or `/api/health` shows `"libass": false` | Your ffmpeg lacks libass. Install the tap build shown [above](#ffmpeg-with-libass). |
| Startup log: *"ffmpeg/ffprobe not found on PATH"* | Install ffmpeg, or start the server from a shell where `which ffmpeg` works. |
| `ImportError: cannot import name 'UTC' from 'datetime'` | The Python is older than 3.11. Recreate `.venv` with 3.11–3.14. |
| **✨ Generate** says `ANTHROPIC_API_KEY is not set` | The key isn't in the server's environment. Export it, or start with `--env-file .env`. Copying `.env.example` to `.env` is not enough on its own. Otherwise, type the header manually. |
| `Unknown header style '…'` | `RICECLIPPER_HEADER_STYLE` names a file that isn't in `prompts/`. Only `generic-header` ships. |
| Emoji header render fails: *"missing a text or renderable color-emoji font"* | No Apple Color Emoji, which is the case off macOS. Remove the emoji or render on macOS. |
| Upload rejected: *"could not read video"* | ffprobe couldn't parse the file. Check it with `ffprobe <file>`. |
| First transcription hangs or fails offline | The Whisper model is still downloading or can't be reached. Wait, or run once with network access. A smaller `RICECLIPPER_WHISPER_MODEL` downloads faster. |
| `Address already in use` | Something else is on port 8000. Pass `--port <other>`. |
| Calling the API directly: render returns `422` with `"loc":["body"],"msg":"Field required"` | `POST /api/jobs/{id}/render` needs a JSON body even though every field has a default. Send at least `-H 'Content-Type: application/json' -d '{}'`. The UI always sends one. |
| `pytest` / `ruff` / `pre-commit`: command not found | Install `requirements-dev.txt` into the active venv. |

## Test and quality gates

The suite mocks every ffmpeg and Whisper call. It needs no ffmpeg, no network,
no model download, and no API key.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest -q                                   # full suite

python -m ruff check . && python -m ruff format --check .   # lint + format (matches CI)
python -m pytest -m smoke -q                          # the 8-test fast tier
python -m pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85

# Maintainer-only subject-crop fixture gate (needs the gitignored
# fixtures/landscape/ clips; six named roles + sheet review)
python scripts/crop_check.py fixtures/landscape --contact-sheets-approved
```

CI (`.github/workflows/ci.yml`) runs the same ruff checks and the full suite
with an **85% coverage floor** on Python 3.12, on every PR and on every push to
`main`. That job is the required check. A second, non-required job runs the
suite on Python 3.14. `tests/test_gates.py` locks those numbers so they can't silently drift.
Optional local hooks mirror CI. `pre-commit` is pinned in
`requirements-dev.txt`:

```bash
pre-commit install --hook-type pre-commit --hook-type pre-push
```

The hooks run in your active venv, not in an isolated pre-commit environment.
The commit tier runs ruff + the smoke tier; the push tier runs the full suite
and the coverage floor. A `main` branch-protection ruleset is prepared in
`.github/rulesets/` but not yet applied (rulesets need GitHub Pro on a private
repo).

## Repo layout

```
app/              FastAPI app — server + render orchestration endpoints
transcribe/       faster-whisper wrapper → word-level caption lines
render/           ffmpeg + ASS rendering (captions, header, audio mix)
  templates/      ASS caption/header templates
  models/         vendored YuNet face-detection model
web/              static HTML/JS review UI
prompts/          header-generator styles (only generic-header.json is tracked)
scripts/          maintainer subject-crop check and tuning tools
tests/            unit tests + test_gates.py (quality-gate meta-tests)
.riceclipper_work/ app-owned uploaded sources, intermediates, and outputs (gitignored)
.github/          CI workflow, issue/PR templates, pending branch ruleset
pyproject.toml    ruff + pytest configuration
docs/
  spikes/         de-risking investigations (see emoji-burn-in)
  adr/            accepted architecture decision records and amendments
  design/         subject-crop and music-path design specs
  integration/    RiceSearcher → RiceClipper → RicePoster handoff contracts
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
