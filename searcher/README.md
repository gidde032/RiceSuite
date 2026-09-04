# RiceSearcher

Content-sourcing pillar of the Rice harness. On-demand pull discovery +
transcript-driven extraction into a scored, moment-deduplicated candidate-slice
library, handed off to RiceClipper for rendering. **Never posts, publishes, or
uploads content; local-first.** See [SPEC.md](SPEC.md) (decisions D1–D8) and
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
ricesearcher score <id-or-prefix>                 # extract + LLM-score candidate slices
ricesearcher slices                               # list scored candidate slices
ricesearcher dedup [--threshold 0.65]             # advisory possible-duplicate flags
ricesearcher review                               # Slate web UI: the select-and-approve gate
ricesearcher handoff                              # write selected slices as a batch for RiceClipper
```

`ricesearcher review` serves the local review UI at http://127.0.0.1:8765 — browse
scored moments, preview each window, tighten in/out, and Select / Reject. It reads
and annotates the local library only; it never posts, publishes, or uploads.

(`python -m ricesearcher <cmd> …` works identically if you prefer.)

Library data and cached source media live under `~/.ricesearcher` by default
(override with `RICESEARCHER_DATA_DIR`). Completed handoff batches are written to
the separate `~/ricesearcher-handoff` root by default (override with
`RICESEARCHER_HANDOFF_DIR`) for RiceClipper to ingest.

## Development

```bash
ruff format --check . && ruff check . && pytest
pytest -m smoke --no-cov  # fast five-test feedback tier
```

Gates: ruff (format + lint), pytest with a 90% coverage floor, and a pinned smoke
tier. See [CLAUDE.md](CLAUDE.md) for the operating rules and hard safety boundary.
