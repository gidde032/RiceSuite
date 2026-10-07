# Changelog

All notable changes to RiceSearcher are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- The standalone CLI (`ricesearcher score`, `profiles`, `handoff`, ...) now
  uses the paths `rice data location` reports when `RICESEARCHER_DATA_DIR` or
  `RICESEARCHER_HANDOFF_DIR` is unset. After `rice data cutover` it had kept
  reading the stale `~/.ricesearcher` copy (RiceSuite
  [#73](https://github.com/gidde032/RiceSuite/issues/73)).

### Documentation

- Updated the Searcher spec and roadmap to reflect the enforced 90% coverage
  floor and the delivered offline scoring and mypy gate (RiceSuite
  [#68](https://github.com/gidde032/RiceSuite/issues/68)).

### Added

- Searcher review now fills the available width with two score-ordered cards per
  row at 900 px and above, and adaptive preview/metadata layout (RiceSuite #21).
- Compact handoff status shows actual clip preparation and batch commitment,
  with separate participating-card progress. A process-local, profile-scoped
  observation endpoint supports read-only reconnection after a lost response or
  reload; publication without final library confirmation is shown as unconfirmed.
  No mutation is retried by polling, and no durable progress history is added.

- First-time-user README: requirements (Python versions, ffmpeg, disk and model
  downloads), venv install, configuration table, an offline-first first run, how
  each stage works, beat profiles, the RiceClipper handoff layout, and
  troubleshooting.
- `score --offline` (#2): ranks candidates by the heuristic prefilter score with
  no API key and no network call. Offline slices record
  `scorer_model = "heuristic-offline"` and a "not LLM-scored" rationale. `slices`
  marks them `offl`, and the review UI shows an *offline score* badge. The
  README's First run now walks the whole flow offline. `--offline` and `--model`
  are mutually exclusive.
- Handoff manifest clips carry `scorer_model`. This is an additive field; the
  schema version stays 1.
- CI job `tests (Python 3.14)` (not required): runs the test suite on 3.14 and
  checks that the runtime pins resolve there.

### Fixed

- A source deleted, a cache cleared, or a failed pull cleaned up while a pull of
  the same content ran could leave that pull's committed row pointing at a
  removed media file. Each removal now checks references and unlinks under a
  cache custody lock (an flock beside the cache directory) that a pull also
  holds when it commits, and a pull puts back a cached copy removed during its
  transcription (#6).
- `score` with no Anthropic credential now fails before any request with
  `ANTHROPIC_API_KEY is not set; …` instead of the SDK's generic
  "Could not resolve authentication method".
- The test suite ignores `RICESEARCHER_*` variables exported in the developer's
  shell. With `RICESEARCHER_PROFILES_DIR` exported, the packaging test failed and
  six test files seeded `example-beat.json` into that directory.
- `credentials.env.example` no longer lists the retired
  `RICESEARCHER_BEAT_PROFILE`, documents `RICESEARCHER_EMBED_MODEL`, and ships
  the API key commented out, so an unedited copy doesn't send the placeholder.

### Security

- **The review app refuses cross-site requests** (RiceSuite
  [#14](https://github.com/gidde032/RiceSuite/issues/14)). It now runs the
  RiceSuite gateway's Host/Origin guard itself (`ricesuite.localguard`), on
  the port it is bound to (`8791` in RiceSuite, `8765` standalone): a request
  not addressed to `127.0.0.1:<port>` or `localhost:<port>` gets 421, and a
  POST, PATCH or DELETE whose `Origin` is neither the app's own loopback
  origin nor the gateway's (`http://127.0.0.1:8790` or
  `http://localhost:8790`) gets 403, as does `Origin: null`. Requests without
  an Origin (curl, scripts) are unaffected. Tests address the app as
  `http://127.0.0.1:8765`. `ricesearcher review --host` now accepts only
  `127.0.0.1` or `localhost`, the names the guard answers.
- **Pages can't be framed by another site, and cached media runs no script**
  (RiceSuite [#38](https://github.com/gidde032/RiceSuite/issues/38)). Every
  response carries `X-Frame-Options: SAMEORIGIN` and CSP
  `frame-ancestors 'self'`. `/cache/…` is served with
  `X-Content-Type-Options: nosniff` and CSP `default-src 'none'; sandbox`.

## [1.0.0] — 2026-09-21

First public release: the complete RiceSearcher-side v1 flow — acquire/transcribe
→ score/dedup → review/select → handoff — for local, on-demand content sourcing.
RiceSearcher never posts, publishes, or uploads content; it is local-first and
reads sources and writes local files only.

### Added

- **Acquire + transcribe (Phase 1).** `pull <url|file>` acquires a YouTube URL
  (via `yt-dlp`) or a local media file (watch-folder door), caches it
  content-addressed, and transcribes it to word-level timestamps in a local
  SQLite library. `list` / `show` inspect sources and transcripts.
- **Extract + score (Phase 2).** A heuristic prefilter shortlists candidate
  windows; an LLM (Anthropic; `claude-haiku-4-5` by default) scores and explains
  each window against a versioned **beat profile**, producing scored candidate
  slices with padded context and editable target in/out.
- **Advisory dedup (Phase 3).** Intra-source time-overlap and cross-source
  transcript-embedding similarity attach a "possible duplicate" annotation
  (`dedup` CLI, default threshold 0.65). The annotation is **advisory only** — it
  never filters, hides, blocks, or deprioritizes a slice.
- **Slate review UI + select gate (Phase 4).** `ricesearcher review` serves a
  local (`127.0.0.1`) web UI to browse moments, edit in/out anywhere within the
  source, preview the exact selection, and Select/Reject. Includes a
  media-management page (per-source delete, whole-cache clear) with two-step
  confirms. This is the human select-and-approve gate.
- **Handoff writer (Phase 5).** On select, extract exactly the reviewed interval
  (clamped to the true source extent) and write a mirrored, manifest-last
  filesystem handoff batch (schema-1 superset manifest) to
  `RICESEARCHER_HANDOFF_DIR` for RiceClipper to ingest.
- **Saved profiles (D9, ADR-002).** Many beat profiles as JSON files, one per
  file; library, dedup, review, and handoff partition by `profile_id`; sources
  are shared. A generic `example-beat` profile is seeded on first use.
- **Quality gates.** Ruff (format + lint), mypy (Python 3.12), JS syntax +
  behavior tests, and pytest with a 90% coverage floor and a pinned smoke tier,
  enforced locally and in CI.

### Safety

- No code path posts, publishes, or uploads content. Outbound network is limited
  to `yt-dlp` source acquisition and the Anthropic scoring API, both explicitly
  user-invoked. `rights_risk` is tracked per slice. See `SECURITY.md`.

[1.0.0]: https://github.com/gidde032/RiceSearcher/releases/tag/v1.0.0
