# RiceSearcher

Content-sourcing pillar of the Rice harness. On-demand pull discovery +
transcript-driven extraction into a scored, moment-deduplicated candidate-slice
library, handed off to RiceClipper for rendering. **Never posts, publishes, or
uploads content; local-first.** See [SPEC.md](SPEC.md) (decisions D1–D9) and
[ADR-001.md](ADR-001.md) (the three-pillar boundary).

> Status: **v1.0.0 — phases 1–5 complete.** The complete RiceSearcher-side v1
> flow is available: acquire/transcribe → score/dedup → review/select → handoff.
> See [CHANGELOG.md](CHANGELOG.md) for release notes and [ROADMAP.md](ROADMAP.md)
> for routed-forward work.

## How it works

```
pull ──► score ──► dedup ──► review ──► handoff ──► RiceClipper
(fetch +   (prefilter +  (advisory   (you select   (clip files +
 transcribe) LLM score)   flags)      and trim)     manifest.json)
```

1. **Acquire and transcribe (`pull`).** A YouTube URL is downloaded with `yt-dlp`.
   A local media file (`.mp4 .mov .mkv .webm .m4a .mp3 .wav .aac`) is used as-is.
   Either way the media is copied into a content-addressed cache, transcribed
   locally with word-level timestamps by `faster-whisper`, and stored as a
   *source* in a local SQLite library. The source id is the media's content hash,
   so pulling the same bytes again refreshes the same row.
2. **Extract and score (`score`).** A cheap heuristic prefilter splits the
   transcript into utterances (a silence over 1.2 s ends one) and merges them into
   windows of 12–45 s (a leftover shorter window is kept). Each window is ranked by the profile's keywords, questions,
   laughter, exclamations, and length fit, and only the top 12 go to the LLM. The
   Anthropic API scores each one from 0 to 1 against the chosen **beat profile**
   and gives a one-sentence rationale. Each result is stored as a *candidate
   slice* with 2 s of padding for review context. Re-scoring replaces a source's
   untouched candidates and never overwrites a slice you have already reviewed.
3. **Advisory dedup (`dedup`).** Flags slices that overlap by at least half in the
   same source, or whose transcripts embed similarly across sources (cosine
   similarity ≥ 0.65 by default, using a local `sentence-transformers` model). The
   flag is **advisory only**. It never hides, removes, or reorders a slice.
4. **Review (`review`).** A local web UI is the human select-and-approve gate. You
   browse the scored moments, set the exact in/out anywhere within the source,
   preview it, and Select or Reject. Nothing is selected automatically.
5. **Hand off (`handoff`, or *Send selected → RiceClipper* in the UI).** Cuts
   exactly the reviewed interval from each selected slice with `ffmpeg` and writes
   one batch directory for RiceClipper to pick up. See
   [Output: the RiceClipper handoff](#output-the-riceclipper-handoff).

Every slice records its profile id and version, the scorer model, and a
`rights_risk` (`low` for local files, `med` for YouTube). Source material is
usually copyrighted, so keep it off public surfaces.

**Profiles** partition the library. Sources are shared, but each profile keeps
its own scored slices, dedup flags, review state, and handoffs. That is why
`score`, `slices`, `dedup`, and `handoff` all require `--profile`.

## Requirements

- **Python 3.11 or newer** (`requires-python = ">=3.11"`). CI runs every gate on
  3.12, the reference version. A second CI job runs the tests on 3.14 and checks
  that the runtime pins resolve there. The full dependency set has also been
  installed and tested locally on 3.14. Every native dependency publishes wheels
  for 3.11 and 3.13, but CI does not test those versions.
- **`ffmpeg` and `ffprobe`** on your `PATH`. yt-dlp needs ffmpeg to merge
  YouTube's separate video and audio streams, and `handoff` needs both to cut and
  measure clips. `pull` uses `ffprobe` to read a local file's duration.
  faster-whisper decodes audio with its own bundled FFmpeg libraries. Install
  with `brew install ffmpeg` (macOS) or `sudo apt install ffmpeg`
  (Debian/Ubuntu).
- **Node.js 18+**, only for running the review UI's JavaScript tests.
- **Disk space.** The virtual environment is about 1.3 GB because
  sentence-transformers pulls in PyTorch. On first use, models download to the
  Hugging Face cache (`~/.cache/huggingface`, or set `HF_HOME` to move it): the
  Whisper model on the first `pull` (the default `small` is about 480 MB, `tiny`
  about 75 MB), and the ~90 MB embedding model on the first `dedup` run that has
  scored slices to compare.
- **An Anthropic API key**, needed only for `score`.

## Install

```bash
git clone https://github.com/gidde032/RiceSearcher.git
cd RiceSearcher
python3 -m venv .venv            # any Python >= 3.11; e.g. python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -e .                    # installs the `ricesearcher` command
pip install -r requirements.txt     # yt-dlp, faster-whisper, anthropic, FastAPI, sentence-transformers
pip install -r requirements-dev.txt # ruff, mypy, pytest (for the gates)
ricesearcher --help
```

The core package and test suite import the heavy adapters (yt-dlp,
faster-whisper, sentence-transformers) lazily. With only
`requirements-dev.txt` installed you can run the gates, but not `pull` or
`dedup`.

## Configure

Everything is set through environment variables. You can export them, or put
them in a `credentials.env` file (gitignored). Start from the template:

```bash
cp credentials.env.example credentials.env   # then edit it
```

RiceSearcher reads `credentials.env`, then `.env`, **from the current working
directory** each time a command starts. Run commands from the directory that holds
the file. A variable already exported in your shell always wins over the file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | *(none)* | Required by `score`. Nothing else calls a paid API. |
| `RICESEARCHER_SCORER_MODEL` | `claude-haiku-4-5` | Scorer model (lowest cost). `score --model` overrides it. |
| `RICESEARCHER_DATA_DIR` | `~/.ricesearcher` | Library (`library.sqlite3`), media cache (`cache/`), and profiles. |
| `RICESEARCHER_PROFILES_DIR` | `<data_dir>/profiles` | Where profile JSON files live. |
| `RICESEARCHER_HANDOFF_DIR` | `~/ricesearcher-handoff` | Where handoff batches are written for RiceClipper. |
| `RICESEARCHER_EMBED_MODEL` | `all-MiniLM-L6-v2` | sentence-transformers model used by `dedup`. |

To try RiceSearcher without touching a real library or RiceClipper's inbox,
point the data and handoff directories somewhere disposable first:

```bash
export RICESEARCHER_DATA_DIR=/tmp/rs-trial/data
export RICESEARCHER_HANDOFF_DIR=/tmp/rs-trial/handoff
```

## First run

Start with a local file. The pull step needs no API key, and apart from the
one-time Whisper model download it makes no network calls:

```bash
ricesearcher profiles                        # seeds and lists the bundled `example-beat` profile
ricesearcher pull ./interview.mp4 --model tiny   # small, fast model for a first try
ricesearcher list                            # note the 12-character source id
ricesearcher show <id-or-prefix>             # print the transcript
```

With `ANTHROPIC_API_KEY` set, score the source, then review and hand off:

```bash
ricesearcher score <id-or-prefix> --profile example-beat
ricesearcher slices --profile example-beat   # score, dup flag, rights_risk, window, text
ricesearcher dedup --profile example-beat    # optional; first run with slices downloads the embedding model
ricesearcher review                          # open http://127.0.0.1:8765, Select some slices
ricesearcher handoff --profile example-beat  # or use the UI's "Send selected → RiceClipper"
```

A source shorter than 12 s is still scored, but its window ranks lower in the
prefilter. A source with no transcribed words (silence, music only) scores 0
slices.

> **No offline scoring mode yet.** `score` always calls the Anthropic API, and
> `slices`, `dedup`, `review`, and `handoff` all need scored slices. Without a key
> you can try `profiles`, `pull` (local files), `list`, and `show`, and run the
> test suite, which uses fakes. An offline heuristic-only scorer is tracked in
> [#2](https://github.com/gidde032/RiceSearcher/issues/2).

## Usage

```bash
# Quote YouTube URLs — the `?` in a URL is a shell glob character (zsh will
# otherwise error "no matches found" before the command even runs):
ricesearcher pull "https://www.youtube.com/watch?v=VIDEO_ID"
ricesearcher pull "https://youtu.be/VIDEO_ID"     # short form also works
ricesearcher pull ./interview.mp4                 # local file (watch-folder door)
ricesearcher pull ./interview.mp4 --model medium  # any faster-whisper size (default: small)
ricesearcher list                                 # list library sources
ricesearcher show <id-or-prefix>                  # print a source's transcript
ricesearcher profiles                             # list saved profiles and their counts
ricesearcher score <id-or-prefix> --profile ID    # extract + LLM-score under one profile
ricesearcher score <id-or-prefix> --profile ID --model claude-sonnet-4-6  # pricier scorer
ricesearcher slices --profile ID [--source ID]    # list scored candidate slices in a profile
ricesearcher dedup --profile ID [--threshold 0.65]  # advisory possible-duplicate flags
ricesearcher review [--host 127.0.0.1] [--port 8765]  # Slate web UI: the select-and-approve gate
ricesearcher handoff --profile ID                 # write a profile's selected slices as a batch
```

`python -m ricesearcher <cmd> …` works identically. Every command exits `0` on
success and `2` with a one-line `error:` message on failure.

### Beat profiles

A profile is a JSON file named `<id>.json` in the profiles directory. The id must
be lowercase letters, digits, and hyphens, up to 40 characters. The first
`profiles` or `score` run seeds `example-beat.json`. To make your own, copy it and
edit it:

```bash
cp ~/.ricesearcher/profiles/example-beat.json ~/.ricesearcher/profiles/my-beat.json
```

```json
{
  "version": "2026-09-22",
  "name": "my-beat",
  "brief": "What makes a moment clippable for this beat (sent to the scorer).",
  "keywords": ["words", "that", "boost", "the prefilter"],
  "positive_examples": ["A short description of a good clip."],
  "negative_examples": ["A short description of a bad clip."]
}
```

`version`, `name`, and `brief` are required. The three lists are optional. The
`version` string is stamped on every slice the profile scores, so bump it when you
change the brief. `profiles` skips a malformed file with a warning.

### The review UI

`ricesearcher review` serves the local review UI at http://127.0.0.1:8765 (Ctrl-C
to stop). Pick a profile to browse its scored moments. You can set any valid in/out
interval within the source, preview that exact selection, and Select / Reject.
**Send selected → RiceClipper** runs the same handoff as the CLI. The **Profiles**
page shows each profile's counts. The **Media** page deletes one source (with its
cached media and slices) or clears the whole cache, and asks you to confirm twice.
The UI reads and annotates the local library only. It never posts, publishes, or
uploads. Keep it on `127.0.0.1`.

## Output: the RiceClipper handoff

Each handoff writes one batch under `RICESEARCHER_HANDOFF_DIR`:

```
~/ricesearcher-handoff/
  batch_20260922_181500_a1b2c3/
    clip_1.mp4
    clip_2.mp4
    manifest.json      # written last, by atomic rename
```

`manifest.json` (schema version 1, `"producer": "ricesearcher"`) lists each clip
with its source reference and title, the source window it was cut from, its
duration, the transcript text inside the window, and the score, rationale,
`rights_risk`, and profile id and version. RiceSearcher only ever writes new
batch directories. A batch is complete only once `manifest.json` exists, so a
reader never sees a half-written batch.

RiceClipper picks batches up from its Searcher inbox, `RICECLIPPER_SEARCHER_INBOX`,
which also defaults to `~/ricesearcher-handoff`. RiceClipper transcribes and
renders the clips. If you change either setting, set the other to the same path.
Don't point `RICESEARCHER_HANDOFF_DIR` at `~/riceclipper-handoff`. That is
RiceClipper's output to RicePoster. See
[`docs/integration/riceclipper-pickup-plan.md`](docs/integration/riceclipper-pickup-plan.md).

Handed-off slices are marked in the library. A failed handoff leaves no partial
batch, and the slices stay selected so you can retry.

## Troubleshooting

| Symptom | Cause and fix |
| --- | --- |
| `zsh: no matches found: https://…?v=…` | Quote the URL. `?` is a shell glob character. |
| `error: no acquirer can handle: '…'` | The path doesn't exist, or the file extension isn't one of the supported media types, or the text doesn't look like a URL. |
| `pull` of a URL fails asking for ffmpeg, or `handoff` fails with `No such file or directory: 'ffmpeg'` / `'ffprobe'` | Install ffmpeg (see [Requirements](#requirements)). Local files still pull without `ffprobe`, but record a duration of 0. |
| `error: pull failed: no decodable audio stream in …` | The file has no audio track. Transcription needs audio. |
| `ModuleNotFoundError: No module named 'faster_whisper'` / `'yt_dlp'` / `'sentence_transformers'` | Run `pip install -r requirements.txt` in the active venv. |
| `Warning: You are sending unauthenticated requests to the HF Hub` | Harmless. It appears during the one-time model download. |
| `error: scoring failed: ANTHROPIC_API_KEY is not set; …` | Export the key, or add it to `credentials.env` and run from the directory that holds that file. |
| `error: scoring failed: Error code: 401 … authentication_error …` | The key was sent but rejected. Check for a typo, a revoked key, or the template's `sk-ant-...` placeholder left in `credentials.env`. |
| `score` reports `scored 0 slices` | The transcript is empty. Check it with `show`: `(no transcript)` means Whisper heard no speech. |
| `error: profile 'x': … No such file or directory` | There is no `x.json` in the profiles directory. Run `ricesearcher profiles` to see what exists. |
| `review` fails with `address already in use` | Another process has port 8765. Use `ricesearcher review --port 8766`. |
| RiceClipper doesn't see a batch | Check that `RICESEARCHER_HANDOFF_DIR` and RiceClipper's `RICECLIPPER_SEARCHER_INBOX` are the same path, and that the batch has a `manifest.json`. |
| `ricesearcher: command not found` | Activate the venv (`source .venv/bin/activate`) and run `pip install -e .`. |

## Development

```bash
ruff format --check . && ruff check . && mypy && pytest
for f in ricesearcher/web/static/*.js; do node --check "$f"; done
node --test tests/js/*.test.js
pytest -m smoke --no-cov  # fast five-test feedback tier
```

Gates: Ruff (format + lint), mypy for the production package targeting Python
3.12, JS syntax and behavior tests, pytest with a 90% coverage floor, and a pinned
smoke tier. Mypy uses standard checking of annotated functions; missing-import
exceptions are limited to the three optional heavy adapters. The tests use fakes
for yt-dlp, Whisper, the scorer, and the embedder, so they need no network, API
key, or model downloads. See [CLAUDE.md](CLAUDE.md) for the operating rules and
hard safety boundary.

## Security & responsible use

RiceSearcher is local-first and **never posts, publishes, or uploads content**.
The only outbound calls are user-invoked `yt-dlp` acquisition, the Anthropic
scoring API, and one-time model downloads from the Hugging Face Hub. Keep the
review server on `127.0.0.1`, never commit API keys, and mind the rights of
copyrighted source material. See [SECURITY.md](SECURITY.md).

## License

Released under the [MIT License](LICENSE).
