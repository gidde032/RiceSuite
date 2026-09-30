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
automatically over the existing filesystem handoff contracts, up to Poster's
inbox, which the maintainer pulls from (ADR-001 amendment of 2026-09-29). The three human
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
| Transport | Filesystem handoffs, auto-ingested by Clipper; Poster ingests on the maintainer's Pull | Q7, Q12 (amended 2026-09-29) |
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
  interface. Every listener, the gateway and each pillar alike, runs the same
  guard (`ricesuite/localguard.py`), because Poster's API is unauthenticated
  and a browser page can reach a pillar's own loopback port without passing
  the gateway (#14):
  - It answers only requests addressed to `127.0.0.1:<port>` or
    `localhost:<port>`, where `<port>` is the port the listener is bound to
    (the gateway's is its configured port). Anything else gets 421
    (DNS-rebinding guard). A pillar run standalone on its old port is
    therefore guarded too.
  - It refuses with 403 any state-changing request (not GET, HEAD or OPTIONS),
    and closes any WebSocket handshake, whose `Origin` is not the listener's own
    loopback origin or, for a pillar, the gateway's
    (`http://127.0.0.1:8790`, `http://localhost:8790`; the launcher passes the
    gateway port it uses in `RICESUITE_GATEWAY_PORT`). `Origin: null` is
    refused. A request without an Origin is allowed, so the launcher, the stop
    guard, `rice status` and curl keep working. A refused WebSocket
    handshake (foreign Host or Origin) is closed before it is accepted, which
    the server answers with 403.
  - Every response says only the listener's own origin may frame it
    (`X-Frame-Options: SAMEORIGIN`, CSP `frame-ancestors 'self'`), so another
    site cannot frame a page and trick the maintainer into clicking it; the
    shell frames each pillar page from the gateway's own origin (#38).
  - Files a user or a download supplied (Poster `/api/media/`, Clipper
    `/api/jobs/…/source` and `/output`, Searcher `/cache/`) are served with
    `X-Content-Type-Options: nosniff` and CSP `default-src 'none'; sandbox`,
    so such a file opened directly runs no script as a suite origin (#38).
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
  it moves `sessions/`, `debug/`, `media/`, `queue.jsonl`, `queue_media/`,
  `history.jsonl` and the in-flight run marker of FR-16, and nothing else. It is never `.resolve()`d (session paths
  reach Chrome as strings). It is ignored under pytest.

### Front door

- **FR-10** The gateway serves the Slate shell at `/`, and each pillar's
  existing page under its own prefix. A pillar page's API calls reach only its
  own pillar. Each pillar frontend gets only the minimal base-path change
  needed for that (Q11: no rebuilt UI).
  The common Slate palette and base surface rules live in one packaged suite
  stylesheet; each document loads it before its own layout and state rules.
- **FR-11** The home view shows, per stage, the batches waiting: Searcher
  batches not yet ingested by Clipper, Clipper batches not yet sent or held by
  a failed render, and batches waiting in Poster's inbox.

### Automatic transport (Q12, Poster clause amended 2026-09-29)

The handoff contracts are unchanged: batch schema, `manifest.json` written
last, FIFO by `created_at`, dedupe by stable `batch_id`, producers only write.

- **FR-12 Searcher → Clipper.** "Send selected" stays the batch boundary. A
  complete Searcher batch (its `manifest.json` present) is ingested by Clipper
  automatically, with no Pull click, and its clips start transcribing.
  Incomplete batches (no manifest) are never ingested. A batch id already
  ingested is never ingested twice.
- **FR-13 Clipper → Poster.** Clipper sends a batch automatically only when
  every clip in it has rendered successfully. A failed render holds the whole
  batch until the clip is fixed and re-rendered; then the batch sends. A clip
  edited after its render request (any card control, or a generated header)
  counts as unrendered until it renders again, so the MP4 always matches the
  header and transcript sent with it. The Send button follows the same rule:
  it refuses while any clip is unrendered, failed, or edited since its render,
  names those clips, and never sends part of a batch. Removing a clip from the
  batch is the reviewer's way to send the rest.
- **FR-14 Poster ingest.** A complete Clipper batch is ingested only when the
  maintainer clicks **Pull from Clipper**, never on its own, even when Poster's
  draft workspace is empty (ADR-001 amendment "Manual Poster ingest",
  2026-09-29). Until then it waits, visibly, in an inbox on the Post tab, and
  the Pull button shows how many batches wait. Pull confirms before
  overwriting unposted drafts (preserves Poster's confirm-before-discard), and
  recovers an unacknowledged batch before taking a new one. Captions generate
  on ingest as today.
- **How FR-12 – FR-14 are driven.** Clipper's batch and Poster's drafts live
  in their pages (transcript edits, headers and captions are browser state),
  so each consumer's page drives its own transport: Clipper polls a read-only
  inbox endpoint (`GET api/searcher-inbox`) and then performs exactly the pull
  or send its button performs. Poster polls `GET api/handoff/inbox` only to
  report waiting batches; it never pulls from the poll.
  Clipper pulls only when nothing unsent would be displaced: the workspace is
  empty, or it holds exactly what was last sent (a later edit or re-render
  holds it). The Pull button follows the same rule, and one pull runs at a
  time, whether the button or the timer started it. One Searcher batch stays
  one Clipper batch; a batch sends itself at most once, and sending it again
  takes the reviewer's confirmation.
  Clipper sends each clip to Poster once: a send that holds a clip already
  sent gets 409 with `already_sent`, unless the reviewer confirmed a second
  send (`resend: true`), so a second tab or a reload cannot send a batch
  twice. Each send carries a key (`send_key` on `POST api/handoff`); a retry
  of an unchanged batch after a lost reply reuses it and gets the batch that
  key already wrote (`replayed: true`). A batch changed since is a new
  send. A pull carries a key too (`pull_key`), so a retry after a lost reply
  gets the same batch back, never the next one. A pulled Searcher batch stays
  open in Clipper until it is sent or the reviewer discards it (Start over, or
  removing every clip). Each tab
  keeps its own pulled batch in `sessionStorage` and restores it after a
  reload (`GET api/workspace?batch_id=`). An open batch the tab does not hold
  (another tab's, or a closed tab's) is named on the page and opens there
  only on a Pull click. Browser-only edits are still lost on a reload.
  Poster's only ingest is the Pull from Clipper button
  (`POST api/pull-from-clipper`), which replays an unacknowledged batch before
  taking the next one. The shell loads all three pages up front and keeps them
  alive, so Clipper's transport runs whenever RiceSuite is open (Q18: v1 runs
  while open). Limits of page-driven transport (background-tab throttling, a
  sleeping laptop, a second open tab) are tracked in #16; handoff folders are
  durable, so a delay loses nothing.
- **FR-15 Never auto-post.** No transport step posts, schedules, or discards a
  draft. Post All and Schedule remain explicit human actions in Poster.

### Crash and stop (Q17)

- **FR-16** A Poster post in flight when Poster's process dies is recorded as
  **unconfirmed** and is never retried automatically, whether it was a manual
  Post All run or a scheduled batch. Manual runs: an in-flight marker in the
  data root, turned into unconfirmed History rows at once when the run is cut
  off in-process (an exception or a cancellation at shutdown) or its results
  cannot be written to History, or on the next start after a process death.
  While History cannot be written at all, the marker stays. Scheduled runs: RicePoster's existing running →
  interrupted startup sweep. (A process killed in the instant between writing
  a run's History rows and removing the marker can leave an extra unconfirmed
  row for that run: accepted, it errs towards checking.)
- **FR-17** `rice stop` refuses, exits non-zero and changes nothing, while a
  Poster posting run is active, unless `--force` is given. If the launcher
  cannot confirm Poster is idle, it treats the run as possibly active: an
  error status, a body that is not the expected JSON object, or an absent
  field all count as unconfirmed. The check takes Poster's **stop hold**
  (`POST api/stop-hold`): Poster refuses it while a run is active, and while
  it is set Poster refuses every new posting run, manual or scheduled, so no
  run can start between the check and the stop. A stop that is refused, or
  that cannot signal or end its processes, releases the hold
  (`DELETE api/stop-hold`); otherwise it ends after a 120-second lease. A
  due scheduled batch skipped under the hold stays pending and fires on the
  next start (FR-18). Pillars run in their own sessions, so a terminal Ctrl-C
  reaches only the launcher, which applies the same check; a second Ctrl-C
  within 10 seconds forces the stop. A SIGTERM sent to the launcher directly
  (what `rice stop` sends after its check, and what an OS shutdown sends)
  stops at once. Launcher liveness is a lock the launcher holds, never a
  recorded pid, so a stale state file or a reused pid is never mistaken for
  RiceSuite. A starting launcher removes a stale state file before it spawns
  anything, and it rewrites the state after every spawn and restart, so each
  running child is recorded. (A launcher killed in the instant between a spawn
  and that write can leave one child unrecorded: accepted.) Processes left
  behind by a launcher that was killed are reported by `rice status` and
  stopped by `rice stop` through the same check.
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
| Poster gates | pytest ≥ 43%; exactly six smoke tests, JUnit execution time < 2s, wall-clock hang detector < 10s | Q16; maintainer-approved smoke budget adjustment (2026-09-29) |
| Suite gates | ruff lint + format on `ricesuite/` and `tests/`, pytest ≥ 90% on `ricesuite` | Q16 |
| Floors | Per pillar, never averaged, never lowered without maintainer sign-off | Q16 |
| Network | Suite tests make no network calls; the Anthropic call-site tests use a loopback stub behind a socket guard | Q16 |
| Data | Tests and agent smoke runs use temp dirs only | Q10 |
| Real accounts | Agents open a real Instagram profile (login, status/health check, probe, layout check, or debugging, not only posting) only with the maintainer's explicit sign-off for that occasion; one open per task, never looped, polled, or repeatedly opened and closed; offline verification uses fakes and temporary profiles | Q15, root `CLAUDE.md` rule 7 |

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
| 3 Auto-transport | #3 | FR-12 – FR-16, full-chain mock-mode test | Batches flow between tabs with no plumbing clicks (Post's Pull is manual since the ADR-001 amendment of 2026-09-29) |

## 7. Out of scope (post-burn-in Issues)

Background login service (#4, Q18); deduplicating transcription (#5), Slate CSS
(#6) and Anthropic clients (#7); model upgrades (#8); desktop wrapper (#9);
unified data directory (#10). Hosted or LAN deployment is excluded outright
(fact 3).

The 2026-09-29 post-burn-in amendment to ADR-001 authorizes #5, #6, #7, and
#10 as follow-ups. Their implementation and delivery state belongs to their
Issues and draft PRs; this original phase outline remains the v0.1 contract.

## 8. Post-burn-in data amendment (#10)

The 2026-09-29 amendment to ADR-001 supersedes the legacy defaults in FR-8 and
FR-9 after an explicit migration. Fresh installations default to
`~/.ricesuite/{searcher,clipper,poster,handoff/searcher-to-clipper,handoff/clipper-to-poster}`.
`RICESUITE_DATA_DIR` selects another absolute root. Existing installations
keep their old effective paths until `rice data cutover` completes; an
ordinary start never migrates data. Explicit per-pillar overrides remain
effective, and contradictory handoff ends or overlapping roots are refused.
The offline `rice data plan`, `copy`, `cutover` and `rollback` commands and their
preconditions are documented in [the migration guide](docs/data-migration.md).
Originals remain in place. Rollback after new activity is refused without
manual reconciliation. No transport or human gate changes under #10.
