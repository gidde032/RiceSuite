# RiceSuite — Suite Specification

**Status:** draft for maintainer review (Phase 1, #1). Derived strictly from
[ADR-001](docs/adr/ADR-001-ricesuite-consolidation.md). Where this document and
the ADR disagree, the ADR wins and this document is wrong.

**Scope:** suite-level behaviour only — the launcher, gateway, shell,
configuration, automatic transport, crash/stop handling and gates. Each
pillar's own `SPEC.md` / ADRs stay authoritative for pillar-internal behaviour,
except where ADR-001 overrides them (Q12 transport, Q14 config, Q10 Poster data
root).

---

## 1. Overview

RiceSuite is the three Rice pillars run as one local app: one `rice` command
starts a single localhost front door (the gateway) and three supervised pillar
processes sharing one Python environment. Batches move between pillars
automatically over the existing filesystem handoff contracts. The three human
judgement gates stay exactly where they are, and nothing is ever posted
automatically.

**Success (ADR-001 Q9 burn-in exit):** at least 5 real posting days across at
least 7 calendar days, each running the full chain (pull → select → render →
live post) in RiceSuite, with at least one scheduled batch firing on its own,
and no fallback to an old app needed.

## 2. Architecture

| Component | What it is | ADR |
|---|---|---|
| `rice` CLI | `rice` / `rice start`, `rice stop`, `rice status` | Q5 |
| Launcher + supervisor | Starts the three pillar processes and the gateway; restarts a crashed pillar | Q6, Q17 |
| Gateway | One localhost-only HTTP port; routes each tab to its own pillar | Q6, Q11 |
| Slate shell | Top bar with Search / Clip / Post tabs, plus a home view of batches waiting at each stage | Q11 |
| Pillars | `searcher/`, `clipper/`, `poster/`, each its own process, unchanged except as ADR-001 overrides | Q6, Q13 |
| Transport | Filesystem handoffs, auto-ingested by each consumer | Q7, Q12 |
| Config | One `ricesuite.env` at the suite root | Q14 |

### 2.1 Ports

| Listener | Port | Binding |
|---|---|---|
| Gateway (the one URL the maintainer opens) | 8790 | 127.0.0.1 |
| Searcher (internal) | 8791 | 127.0.0.1 |
| Clipper (internal) | 8792 | 127.0.0.1 |
| Poster (internal) | 8793 | 127.0.0.1 |
| Old apps (must be free, see FR-4) | 8765 / 8000 / 1738 | — |

Internal ports differ from the old apps' ports so that a running old app and
RiceSuite can never be mistaken for each other.

## 3. Functional requirements

Each requirement is written so a test can check it. "The launcher" means the
`rice` process.

### Launcher and supervision

- **FR-1** `rice` and `rice start` start the gateway and all three pillars in
  the foreground and keep running until stopped (Q18: no background service in
  v1).
- **FR-2** `rice stop` stops a running suite; `rice status` reports, for the
  gateway and each pillar, whether it is running, its port, and its restart
  count.
- **FR-3** Every listener binds to 127.0.0.1 only. No option binds to another
  interface. The gateway answers only requests addressed to
  `127.0.0.1:<port>` or `localhost:<port>` (DNS-rebinding guard) and refuses
  state-changing requests carrying another origin, because Poster's API is
  unauthenticated. This covers traffic through the gateway only: each pillar
  still listens on its own loopback port, exactly as the old apps did
  (hardening tracked in #14).
- **FR-4** The launcher refuses to start, and exits non-zero naming the port,
  if anything is accepting connections on 8765, 8000 or 1738 (an old app may be
  running; only one side runs at a time, Q10). It also refuses if a suite port
  in §2.1 is already taken.
- **FR-5** The supervisor restarts a pillar whose process exits unexpectedly,
  with backoff, and records the restart. The other pillars are not
  restarted.
- **FR-6** Poster runs without `--reload` and with the same event loop and
  HTTP implementation it ran with in RicePoster (asyncio + h11); the shared
  environment must not silently change them.

### Configuration

- **FR-7** One `ricesuite.env` at the suite root holds all configuration. Every
  existing variable name is kept and passed to its pillar unchanged; one
  `ANTHROPIC_API_KEY` serves all three. A variable exported in the shell wins
  over the file. `RICESUITE_ENV` (shell only) selects another file.
- **FR-8** The launcher sets all four handoff variables itself from one value
  per stage: Searcher→Clipper (`RICESEARCHER_HANDOFF_DIR` =
  `RICECLIPPER_SEARCHER_INBOX`, default `~/ricesearcher-handoff`) and
  Clipper→Poster (`RICECLIPPER_HANDOFF_DIR` = `HANDOFF_DIR`, default
  `~/riceclipper-handoff`). Contradictory ends of one stage, or one directory
  for both stages, are refused at startup.
- **FR-9** Poster's data root is configurable with `RICEPOSTER_DATA_DIR`
  (Q10). Unset, every Poster path is byte-identical to RicePoster's
  repository-relative layout. Set, it must be an existing absolute directory;
  it moves `sessions/`, `debug/`, `media/`, `queue.jsonl`, `queue_media/` and
  `history.jsonl`, and nothing else. It is never `.resolve()`d (session paths
  reach Chrome as strings). It is ignored under pytest.

### Front door

- **FR-10** The gateway serves the Slate shell at `/`, and each pillar's
  existing page under its own prefix. A pillar page's API calls reach only its
  own pillar. Each pillar frontend gets only the minimal base-path change
  needed for that (Q11: no rebuilt UI).
- **FR-11** The home view shows, per stage, the batches waiting: Searcher
  batches not yet ingested by Clipper, Clipper batches not yet sent or held by
  a failed render, and batches waiting in Poster's inbox.

### Automatic transport (Q12)

The handoff contracts are unchanged: batch schema, `manifest.json` written
last, FIFO by `created_at`, dedupe by stable `batch_id`, producers only write.

- **FR-12 Searcher → Clipper.** "Send selected" stays the batch boundary. A
  complete Searcher batch (its `manifest.json` present) is ingested by Clipper
  automatically, with no Pull click, and its clips start transcribing.
  Incomplete batches (no manifest) are never ingested. A batch id already
  ingested is never ingested twice.
- **FR-13 Clipper → Poster.** Clipper sends a batch automatically only when
  every clip in it has rendered successfully. A failed render holds the whole
  batch until the clip is fixed and re-rendered; then the batch sends.
- **FR-14 Poster ingest.** A complete Clipper batch is ingested automatically
  only when Poster's draft workspace is empty. Otherwise it waits, visibly, in
  an inbox on the Post tab, and is never merged into or replaces existing
  drafts without the maintainer's action (preserves Poster's
  confirm-before-discard). Captions generate on ingest as today.
- **How FR-12 – FR-14 are driven.** Clipper's batch and Poster's drafts live
  in their pages (transcript edits, headers and captions are browser state),
  so each consumer's page drives its own transport: it polls a read-only inbox
  endpoint (`GET api/searcher-inbox` on Clipper, `GET api/handoff/inbox` on
  Poster) and then performs exactly the pull or send its button performs.
  Clipper pulls only when nothing unsent would be displaced (so one Searcher
  batch stays one Clipper batch); Poster pulls only when its manual Pull would
  not have to ask before overwriting a draft. The shell loads all three pages
  up front and keeps them alive, so transport runs whenever RiceSuite is open
  (Q18: v1 runs while open).
- **FR-15 Never auto-post.** No transport step posts, schedules, or discards a
  draft. Post All and Schedule remain explicit human actions in Poster.

### Crash and stop (Q17)

- **FR-16** A Poster post in flight when Poster's process dies is recorded as
  **unconfirmed** and is never retried automatically, whether it was a manual
  Post All run or a scheduled batch. Manual runs: an in-flight marker in the
  data root, turned into unconfirmed History rows on the next start. Scheduled
  runs: RicePoster's existing running → interrupted startup sweep.
- **FR-17** `rice stop` refuses, exits non-zero and changes nothing, while a
  Poster posting run is active, unless `--force` is given. If the launcher
  cannot confirm Poster is idle, it treats the run as possibly active. Pillars
  run in their own sessions, so a terminal Ctrl-C reaches only the launcher,
  which applies the same check; a second Ctrl-C within 10 seconds forces the
  stop. A SIGTERM sent to the launcher directly (what `rice stop` sends after
  its check, and what an OS shutdown sends) stops at once. Launcher liveness is
  a lock the launcher holds, never a recorded pid, so a stale state file or a
  reused pid is never mistaken for RiceSuite; processes left behind by a
  launcher that was killed are reported by `rice status` and stopped by
  `rice stop` through the same check.
- **FR-18** `rice stop` warns, naming the batches, when a scheduled batch is
  overdue or due within the next 30 minutes, and says it will fire on the next
  start (Poster's startup catch-up). The warning does not block the stop.

### Boundaries

- **FR-19** `searcher/` and `clipper/` never import Playwright or any Poster
  module (checked in CI, statically and at runtime).
- **FR-20** No suite code contacts Instagram, TikTok, or any posting surface.

## 4. Non-functional requirements

| Budget | Value | Source |
|---|---|---|
| Python | ≥ 3.12, one venv, required CI on 3.12, informative job on 3.14 | Q6, fact 1 |
| Searcher gates | ruff format + lint, mypy, JS syntax + tests, pytest ≥ 90% | Q16 |
| Clipper gates | ruff lint + format, pytest ≥ 85% | Q16 |
| Poster gates | pytest ≥ 43% | Q16 |
| Suite gates | ruff lint + format on `ricesuite/` and `tests/`, pytest ≥ 90% on `ricesuite` | Q16 |
| Floors | Per pillar, never averaged, never lowered without maintainer sign-off | Q16 |
| Network | Suite tests make no network calls; the Anthropic call-site tests use a loopback stub behind a socket guard | Q16 |
| Data | Tests and agent smoke runs use temp dirs only | Q10 |

Dependency alignment (fact 1) is the only dependency change: newest
compatible FastAPI / uvicorn / python-multipart / python-dotenv, one
`anthropic` 1.x, Playwright unchanged at RicePoster's version. The root
`requirements.txt` is the single authority; pillar files must accept its pins.

## 5. Contracts

- Handoff contracts: `clipper/docs/integration/riceposter-handoff.md`,
  `clipper/docs/integration/searcher-pickup.md`,
  `searcher/docs/integration/riceclipper-pickup-plan.md`. Unchanged.
- Config contract: `ricesuite.env.example` lists every variable any pillar
  reads (checked by `tests/test_env.py`).

## 6. Phases

Each phase ends in a usable increment and has one Issue, one branch and one
draft PR, stacked.

| Phase | Issue | Delivers | Usable increment |
|---|---|---|---|
| 1 Foundation | #1 | This spec, one venv with aligned pins, `RICEPOSTER_DATA_DIR`, `ricesuite.env` loader, boundary test, one CI | Every pillar runs and passes its gates from one environment and one CI |
| 2 Front door | #2 | `rice` CLI, supervisor, gateway, Slate shell and home view, port refusals, stop rules | One command, one tab for the daily workflow (manual Pull/Send still used) |
| 3 Auto-transport | #3 | FR-12 – FR-16, full-chain mock-mode test | Batches flow between tabs with no plumbing clicks |

## 7. Out of scope (post-burn-in Issues)

Background login service (#4, Q18); deduplicating transcription (#5), Slate CSS
(#6) and Anthropic clients (#7); model upgrades (#8); desktop wrapper (#9);
unified data directory (#10). Hosted or LAN deployment is excluded outright
(fact 3).
