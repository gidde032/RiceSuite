# Changelog

All notable changes to RiceClipper are recorded here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **Auto-header generation (Wave-1).** After transcription, RiceClipper now
  auto-fills the on-screen header from an early frame snapshot plus the reviewed
  transcript via an Anthropic Sonnet vision model (`app/header_gen.py`,
  `render/frame.py`, `POST /api/jobs/{id}/header`). One sentence ending in one or
  two emoji; emoji burn in via the existing Pillow PNG-overlay path. A per-clip
  **Generate** button regenerates with optional guidance (mirrors RicePoster's
  caption regenerate-with-feedback), and manual entry remains the fallback — any
  failure (missing key, API error, undecodable frame) leaves the header field
  manual and never blocks a render. This is the design's only outbound call; it
  generates text and posts nothing (SPEC §3). Prompt styles live in
  `prompts/*.json`: only the neutral `generic-header` seed is tracked, a
  maintainer-specific style stays local and gitignored (pinned by
  `tests/test_prompts.py`), and the default style is chosen by
  `RICECLIPPER_HEADER_STYLE`. Requires `ANTHROPIC_API_KEY`.
- **Pull from RiceSearcher (content-sourcing intake).** RiceClipper now ingests
  batches of selected clips that RiceSearcher writes to `~/ricesearcher-handoff`
  (`RICECLIPPER_SEARCHER_INBOX`), turning each into a normal review job:
  `app/searcher_pickup.py`, `POST /api/pull-from-searcher`, and a "Pull from
  RiceSearcher" button in the review UI. Mirrors RicePoster's pickup discipline —
  manifest-last scan, FIFO, `batch_id` dedupe via a durable consumed registry,
  validate-before-write (malformed → 400, zero writes), and durable custody (clip
  bytes copied into the job dir) before the source batch is removed, with
  rollback on failure. RiceClipper is the intermediary: it *reads* the
  RiceSearcher inbox and still *writes* the separate `~/riceclipper-handoff` for
  RicePoster; RiceSearcher and RicePoster never share a directory. Contract in
  `docs/integration/searcher-pickup.md`.
- **CI and quality gates.** GitHub Actions workflow (`Python 3.12 tests and
  coverage`) runs ruff lint + format checks and the full test suite with an
  **85% coverage floor** on every PR and push to `main`. A two-tier
  `pre-commit` config mirrors it locally (ruff + a 6-test smoke tier on commit;
  full suite + coverage on push). `tests/test_gates.py` locks the gate numbers
  so they cannot drift. A `main` branch-protection ruleset (block force-push +
  deletion, require the PR check) is prepared in `.github/rulesets/main.json`
  but not yet applied: repository rulesets require GitHub Pro for a private
  repo. Ruff config lives in `pyproject.toml`.
- **RicePoster handoff writer (producer side).** A "Send to RicePoster" button
  posts the rendered batch to `POST /api/handoff` (`app/handoff.py`), which
  copies each clip to `clip_<position>.mp4` under a fresh `batch_<ts>/` in the
  handoff root (`RICECLIPPER_HANDOFF_DIR`, default `~/riceclipper-handoff/`) and
  writes `manifest.json` last via an atomic rename. The manifest carries the
  reviewed transcript (for RicePoster's caption grounding), header, and preset
  provenance; it holds no account/slot/posting fields. RiceClipper still writes
  local files only. Implements the producer half of
  `docs/integration/riceposter-handoff.md`; the RicePoster-side pickup is a
  separate effort in that repo.
- **Bounded batch review (queue N, review each).** The review UI now accepts
  several clips in one session: multi-file upload, a review card per clip, and a
  single "Approve & Render All". Transcription and render run strictly one clip
  at a time (client-driven over the existing per-job routes; the server keeps its
  single Whisper model / CPU-bound ffmpeg serialization). Per-clip caption and
  header presets inherit a batch default and can be overridden individually. This
  is the first leg of the RicePoster integration (see
  `docs/integration/riceposter-handoff.md`); the handoff writer is a later phase.
  SPEC §9 updated: the prior "single-clip, no batch" default is superseded; the
  per-clip human-in-the-loop gate is unchanged.

### Changed
- **Slate browser interface.** Reworked the local review UI into the ratified
  dark, compact editing-console layout with rice-grey interaction states,
  treatment-preview cards, the selected rice-and-shears production mark, and
  responsive/accessibility hardening while preserving the existing API,
  render, batch, and handoff contracts.
- **Lyric caption preset addition.** Added four fixed, selectable lyric
  treatments: Lyric Block (italic Avenir Next Condensed with cyan highlight),
  Velvet Serif (Bodoni 72 with red highlight), Powder (DIN Condensed font with
  powder-blue highlight), and Baskerville (with teal highlight). The catalog
  remains bounded; arbitrary font/color editing and user-authored preset
  persistence remain deferred.
- **Visual preset follow-up.** Added seven selectable caption appearances
  (including the original Classic default) and three compact header treatments:
  plain text, black plate, and white plate. The default header is now a
  reference-matched 42px plain overlay with no plate.
- **v1 hardening.** Added manual media-cache clearing, bounded Whisper/ffmpeg
  threading, owned subprocess timeouts and shutdown cleanup, explicit model
  disposal, and stricter job/API error handling.
- Updated project documentation to reflect the implemented v1 slice and the
  resolved color-emoji PNG-overlay path.

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
  The PNG path now applies the selected plain/black-plate/white-plate
  treatment and degrades to the libass header if image rendering fails.

### Fixed
- **Replace-mode duration.** Short replacement music is padded instead of
  allowing ffmpeg's `-shortest` path to truncate the video; regression coverage
  covers music shorter than the source clip.
- **Transcription semaphore cleanup.** faster-whisper's tqdm progress lock now
  uses a thread-only lock, preventing the Conda/uvicorn reload shutdown warning
  about leaked multiprocessing semaphores.
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
