# RiceSearcher

Content-sourcing pillar of the Rice harness. On-demand pull discovery +
transcript-driven extraction into a scored, moment-deduplicated candidate-slice
library, handed off to RiceClipper for rendering. **Never posts, publishes, or
uploads content; local-first.** See [SPEC.md](SPEC.md) (decisions D1–D8) and
[ADR-001.md](ADR-001.md) (the three-pillar boundary).

> Status: **Phase 1** (acquire + transcribe skeleton). Scoring, dedup, the review
> UI, and the handoff writer are later phases — see [ROADMAP.md](ROADMAP.md).

## Install (local, editable)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .                    # installs the `ricesearcher` command
pip install -r requirements.txt     # yt-dlp + faster-whisper (needs ffmpeg)
pip install -r requirements-dev.txt # ruff + pytest (for the gates)
```

`ffmpeg` is a system dependency (used by yt-dlp and faster-whisper).

## Usage

`pull` acquires a source (a YouTube URL via yt-dlp, or a local media file),
caches it content-addressed, transcribes it word-by-word, and stores it in the
local SQLite library.

```bash
ricesearcher pull https://youtu.be/VIDEO_ID   # or: python -m ricesearcher pull ...
ricesearcher pull ./interview.mp4             # local file (watch-folder door)
ricesearcher list                             # list library sources
ricesearcher show <id-or-prefix>              # print a source's transcript
```

Data lives under `~/.ricesearcher` by default (override with
`RICESEARCHER_DATA_DIR`). Nothing is written outside that root.

## Development

```bash
ruff format --check . && ruff check . && pytest
```

Gates: ruff (format + lint), pytest with a 90% coverage floor, and a pinned smoke
tier. See [CLAUDE.md](CLAUDE.md) for the operating rules and hard safety boundary.
