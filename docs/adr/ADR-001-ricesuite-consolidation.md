# ADR-001: Consolidate RiceSearcher, RiceClipper, and RicePoster into RiceSuite

**Status:** ACCEPTED — ratified through an agentic decision challenge
(4 rounds, Q1–Q19), 2026-09-25/26.
**Deciders:** Finn (maintainer)
**Supersedes:** nothing. Complements each pillar's own ADRs and SPECs, which
remain authoritative for pillar-internal behavior (see Source-of-truth below).

## Context

The Rice harness is three public repositories, each a local FastAPI app with
its own port, virtual environment, config file, Slate-themed UI, CI, and agent
operating rules:

| | RiceSearcher | RiceClipper | RicePoster |
|---|---|---|---|
| Job | Acquire → transcribe → score → **human select** | Caption / header / 9:16 crop / music → **human review + render** | **Human Post All / Schedule** to IG/TikTok via real Chrome |
| Port | 8765 | 8000 | 1738 (localhost only, no auth) |
| State | SQLite + cache at `~/.ricesearcher` (~11 GB) | In-memory jobs recovered from `.riceclipper_work/` | `queue.jsonl`, `history.jsonl`, `media/`, `queue_media/`, `sessions/` (~2 GB Chrome profiles) — all under the repo root |
| Claude use | Scorer (`claude-haiku-4-5`, offline option) | Opt-in header (`claude-sonnet-5`) | Captions (`claude-sonnet-4-6`, hardcoded) |
| Gates | ruff, mypy, JS tests, pytest ≥90% | ruff, pytest ≥85%, browser check | pytest ≥43% |

They connect through **filesystem handoffs**: Searcher writes
`~/ricesearcher-handoff/batch_*/`, Clipper pulls it; Clipper writes
`~/riceclipper-handoff/batch_*/`, Poster pulls it. `manifest.json` is written
last (atomicity), batches are FIFO with stable ids, and producers only write.
These contracts are live-verified and are also the safety boundary: Searcher
and Clipper have no code path to any posting surface.

Facts verified during the challenge that constrain the design:

1. **Dependency conflicts.** Clipper pins `fastapi==0.115.6`,
   `anthropic==1.5.0`; Poster pins `fastapi==0.141.1`, `anthropic==0.121.0`.
   Each pillar has exactly one Anthropic call site (`messages.create`), so
   alignment is small. Combined Python floor is **3.12** (Poster).
2. **Poster's scheduler must not die mid-post** (`run.sh` avoids `--reload`
   for this reason), while Searcher loads PyTorch and Clipper runs long ffmpeg
   renders.
3. **Poster's API is unauthenticated and can post to real accounts**; hosted or
   LAN deployment is an explicit Poster non-goal, and posting depends on a real
   Mac Chrome fingerprint.
4. **All three frontends call root-absolute `/api/...` paths** — they collide
   behind one origin without a base-path change.
5. **Poster's data root is hardcoded** to the repo (`PROJECT_ROOT` in
   `backend/config.py`).
6. **Poster's pull already confirms before discarding unposted drafts** (#82).
7. **Public histories are the sanitized ones.** GitHub `main`: Clipper 113
   commits, Searcher 104, Poster 19 (squashed at public release). The local
   `~/Projects/RiceClipper-OG` and `~/Projects/RiceSearcher-OG` hold
   **pre-sanitization** history and must never be imported.

## Decision

RiceSuite becomes the product: a **local browser app launched by one `rice`
command**, a **single Slate front door** in front of **three supervised
pillar processes sharing one Python environment**, batches moving
**automatically over the existing filesystem handoff contracts**, in **one
monorepo imported with history**. The three original repositories are archived
only after a real-use burn-in passes.

## Settled choices

| # | Topic | Decision |
|---|---|---|
| Q1 | Goals (ranked) | One front door > seamless flow > one codebase. Public one-install is a nice-to-have, not a driver. |
| Q2 | Audience | Maintainer first, but stays cloneable and installable from GitHub on macOS and Linux (keep first-time-user install quality). |
| Q3 | Human gates | All three judgement gates stay (Searcher select, Clipper review/render, Poster Post All/Schedule). Only transport between them is automated. **Never auto-post.** |
| Q4 | Old repos | RiceSuite is the product. The three repos stay supported until the burn-in (Q9) passes, then are archived read-only and pointed at RiceSuite; open issues are transferred. Pillars may still run alone as a developer convenience, not a supported mode. |
| Q5 | Runtime | Local server + browser tab, started by one `rice` command. A desktop wrapper can come later on top. Hosted/cloud is excluded (fact 3). |
| Q6 | Process model | One front door (gateway) + three separate pillar processes supervised by the launcher, one shared venv. Crash isolation protects Poster's scheduler; existing test suites stay valid. |
| Q7 | Transport | Keep the filesystem handoff contracts unchanged; each consumer auto-ingests when a complete batch (`manifest.json` present) appears. No shared database or in-memory queue. |
| Q8 | Repo import | Monorepo with full history of each pillar under `searcher/`, `clipper/`, `poster/`, imported **only from GitHub `main`** (never the `-OG` folders). |
| Q9 | Burn-in exit | ≥5 real posting days across ≥7 calendar days; each day the full chain (pull → select → render → live post) runs in RiceSuite; ≥1 scheduled batch fires on its own; no fallback to an old app was needed. During burn-in, old repos take critical fixes only, each ported into RiceSuite. |
| Q10 | Burn-in data | RiceSuite uses existing data in place (Searcher library, Clipper work dir, Poster sessions/queue/history/media, both handoff dirs). Only one side runs at a time: the launcher refuses to start if an old app is serving on 8765 / 8000 / 1738. Poster's data root becomes configurable (required work, fact 5). Unifying data locations is post-burn-in. |
| Q11 | UI | Shared Slate shell: top bar with Search / Clip / Post tabs plus a small **home view** showing batches waiting at each stage. Each tab serves the pillar's existing page, adjusted only so its API calls reach its own pillar (fact 4). No rebuilt single UI. |
| Q12 | Batch advancement | **Searcher:** "Send selected" stays as the batch boundary; the batch appears in Clipper already transcribing, with no Pull click. **Clipper:** auto-sends the batch once every clip in it renders successfully; a failed render holds the batch until fixed and re-rendered. **Poster:** auto-ingests only when the draft workspace is empty; otherwise the batch waits in a visible inbox on the Post tab (preserves fact 6). Captions generate on ingest as today. |
| Q13 | Consolidation depth | Move-and-wire only. Behavior changes are limited to those ratified here (front door, auto-transport, single config, configurable data root). Deduplicating transcription, Slate CSS, and Anthropic clients, and any model changes, are post-burn-in Issues. Dependency alignment needed for one venv (fact 1) is in scope. |
| Q14 | Config | One `ricesuite.env` at the suite root, preserving every existing variable name, a single `ANTHROPIC_API_KEY`. The launcher sets both handoff-directory variables itself so they cannot be mismatched. |
| Q15 | Agent rules | Root `CLAUDE.md` with suite hard rules (never auto-post; only `poster/` touches posting; local-only; merge/release/deploy/visibility reserved to the maintainer). Each pillar directory keeps its own `CLAUDE.md`. An automated **boundary test** fails CI if `searcher/` or `clipper/` imports Playwright or any `poster` module. Same Issue → branch → draft PR → three cold reviewers workflow. Poster's gitignored `SPEC.md`/`CLAUDE.md` notes stay local and gitignored. |
| Q16 | Quality gates | One CI keeps every pillar's current gates and coverage floor (90 / 85 / 43), none weakened, no averaged floor. Add suite-level tests for launcher, gateway, and auto-transport, plus one **full-chain mock-mode test** (local file → select → render → Poster draft; `POST_MODE=mock`; no network, no posting). |
| Q17 | Crash / stop | Launcher auto-restarts a crashed pillar. A Poster post in flight at crash time is recorded **unconfirmed** and never auto-retried. `rice stop` refuses while a post is running unless `--force`, and warns when a scheduled batch is due. |
| Q18 | Background running | v1 runs in the foreground only while open (as today). An optional login service is a post-burn-in Issue. |
| Q19 | Repo visibility | Private during burn-in. Going public, simultaneously with archiving the old repos, is a maintainer-only action. |

## Consequences

**Easier:** one command and one tab for the daily workflow; no Pull/Send
plumbing clicks or mismatched handoff-dir settings; one repo, one venv, one CI.

**Harder / accepted:** a launcher and gateway are new moving parts; three
processes cost more memory than one; duplicated internals (two Whisper
wrappers, three Slate copies) persist until post-burn-in cleanup; during
burn-in, critical fixes land twice.

## Open items this decision depends on

1. **Fingerprint parity (burn-in day 0, maintainer-run):** run
   `tools/probe_fingerprint.py --all-slots` from the old RicePoster and from
   RiceSuite and confirm identical controlled surfaces before the first live
   post from RiceSuite.
2. **Post-burn-in Issues to file:** optional login service (Q18); dedup of
   transcription / Slate / Anthropic clients and model upgrades (Q13);
   desktop wrapper (Q5); unified data directory (Q10). Searcher scheduled
   discovery stays RiceSearcher #7 until transferred.

## Source-of-truth order (inside RiceSuite)

1. Code and tests.
2. This ADR for suite-level structure; `SPEC.md` (suite) once written.
3. Each pillar's own `SPEC.md` / ADRs for pillar-internal behavior, unchanged
   except where this ADR explicitly overrides (Q12 transport, Q14 config,
   Q10 Poster data root).
