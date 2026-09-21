# RiceSearcher

Content-sourcing pillar of the Rice harness. On-demand pull discovery +
transcript-driven extraction into a scored, moment-deduplicated candidate-slice
library, handed off to RiceClipper for rendering. **Never posts, publishes, or
uploads content; local-first.** See [SPEC.md](SPEC.md) (decisions D1–D9) and
[ADR-001.md](ADR-001.md) (the three-pillar boundary).

> Status: **Phases 1–5 merged.** The complete RiceSearcher-side v1 flow is
> available: acquire/transcribe → score/dedup → review/select → handoff.
> See [ROADMAP.md](ROADMAP.md) for routed-forward work.

## Install (local, editable)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                    # installs the `ricesearcher` command
pip install -r requirements.txt     # yt-dlp + faster-whisper (needs ffmpeg)
pip install -r requirements-dev.txt # ruff + pytest (for the gates)
```

`ffmpeg` is a system dependency (used by yt-dlp and faster-whisper).

### API key (scoring only)

`ricesearcher score` calls the Anthropic API — the **only** paid API in the
project. Provide `ANTHROPIC_API_KEY` either by exporting it, or by copying
[`credentials.env.example`](credentials.env.example) to `credentials.env` (gitignored)
and filling it in; RiceSearcher loads that file automatically. The scorer defaults
to the lowest-cost `claude-haiku-4-5`; set `RICESEARCHER_SCORER_MODEL` (or pass
`--model`) for a more capable, pricier model.

## Usage

`pull` acquires a source (a YouTube URL via yt-dlp, or a local media file),
caches it content-addressed, transcribes it word-by-word, and stores it in the
local SQLite library.

```bash
# Quote YouTube URLs — the `?` in a URL is a shell glob character (zsh will
# otherwise error "no matches found" before the command even runs):
ricesearcher pull "https://www.youtube.com/watch?v=VIDEO_ID"
ricesearcher pull "https://youtu.be/VIDEO_ID"     # short form also works
ricesearcher pull ./interview.mp4                 # local file (watch-folder door)
ricesearcher list                                 # list library sources
ricesearcher show <id-or-prefix>                  # print a source's transcript
ricesearcher profiles                             # list saved profiles and their counts
ricesearcher score <id-or-prefix> --profile ID    # extract + LLM-score under one profile
ricesearcher slices --profile ID                  # list scored candidate slices in a profile
ricesearcher dedup --profile ID [--threshold 0.65]  # advisory possible-duplicate flags
ricesearcher review                               # Slate web UI: the select-and-approve gate
ricesearcher handoff --profile ID                 # write a profile's selected slices as a batch
```

Profiles live in `<data_dir>/profiles/<id>.json` (override the directory with
`RICESEARCHER_PROFILES_DIR`). `score`, `slices`, `dedup`, and `handoff` require
`--profile`; each profile keeps its own scored slices, dedup, and handoff.

`ricesearcher review` serves the local review UI at http://127.0.0.1:8765 — browse
scored moments, set any valid in/out interval within the source, preview that exact
selection, and Select / Reject. Handoff exports exactly the saved interval for
RiceClipper to transcribe and render. The UI reads and annotates the local library
only; it never posts, publishes, or uploads.

(`python -m ricesearcher <cmd> …` works identically if you prefer.)

Library data and cached source media live under `~/.ricesearcher` by default
(override with `RICESEARCHER_DATA_DIR`). Completed handoff batches are written to
the separate `~/ricesearcher-handoff` root by default (override with
`RICESEARCHER_HANDOFF_DIR`) for RiceClipper to ingest.

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
exceptions are limited to the three optional heavy adapters. See
[CLAUDE.md](CLAUDE.md) for the operating rules and hard safety boundary.
