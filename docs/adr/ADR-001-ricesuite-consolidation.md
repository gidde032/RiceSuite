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
only after a real-use burn-in passes. *(Burn-in passed 2026-10-05. See
"Burn-in complete; public" below.)*

## Settled choices

| # | Topic | Decision |
|---|---|---|
| Q1 | Goals (ranked) | One front door > seamless flow > one codebase. Public one-install is a nice-to-have, not a driver. |
| Q2 | Audience | Maintainer first, but stays cloneable and installable from GitHub on macOS and Linux (keep first-time-user install quality). |
| Q3 | Human gates | All three judgement gates stay (Searcher select, Clipper review/render, Poster Post All/Schedule). Only transport between them is automated. **Never auto-post.** |
| Q4 | Old repos | RiceSuite is the product. The three repos stay supported until the burn-in (Q9) passes, then are archived read-only and pointed at RiceSuite; open issues are transferred. Pillars may still run alone as a developer convenience, not a supported mode. *(Burn-in passed 2026-10-05; the repos are superseded. See "Burn-in complete; public" below.)* |
| Q5 | Runtime | Local server + browser tab, started by one `rice` command. A desktop wrapper can come later on top. Hosted/cloud is excluded (fact 3). |
| Q6 | Process model | One front door (gateway) + three separate pillar processes supervised by the launcher, one shared venv. Crash isolation protects Poster's scheduler; existing test suites stay valid. |
| Q7 | Transport | Keep the filesystem handoff contracts unchanged; each consumer auto-ingests when a complete batch (`manifest.json` present) appears. No shared database or in-memory queue. *(Poster's ingest amended 2026-09-29: manual only. See "Manual Poster ingest" below.)* |
| Q8 | Repo import | Monorepo with full history of each pillar under `searcher/`, `clipper/`, `poster/`, imported **only from GitHub `main`** (never the `-OG` folders). |
| Q9 | Burn-in exit | ≥5 real posting days across ≥7 calendar days; each day the full chain (pull → select → render → live post) runs in RiceSuite; ≥1 scheduled batch fires on its own; no fallback to an old app was needed. During burn-in, old repos take critical fixes only, each ported into RiceSuite. *(Closed 2026-10-05: burn-in passed. See "Burn-in complete; public" below.)* |
| Q10 | Burn-in data | RiceSuite uses existing data in place (Searcher library, Clipper work dir, Poster sessions/queue/history/media, both handoff dirs). Only one side runs at a time: the launcher refuses to start if an old app is serving on 8765 / 8000 / 1738. Poster's data root becomes configurable (required work, fact 5). Unifying data locations is post-burn-in. *(Data unified 2026-09-29; the port guard now protects against a legacy app started by mistake. See "Post-burn-in amendment" and "Burn-in complete; public" below.)* |
| Q11 | UI | Shared Slate shell: top bar with Search / Clip / Post tabs plus a small **home view** showing batches waiting at each stage. Each tab serves the pillar's existing page, adjusted only so its API calls reach its own pillar (fact 4). No rebuilt single UI. |
| Q12 | Batch advancement | **Searcher:** "Send selected" stays as the batch boundary; the batch appears in Clipper already transcribing, with no Pull click. **Clipper:** auto-sends the batch once every clip in it renders successfully; a failed render holds the batch until fixed and re-rendered. **Poster:** auto-ingests only when the draft workspace is empty; otherwise the batch waits in a visible inbox on the Post tab (preserves fact 6). Captions generate on ingest as today. *(Poster clause amended 2026-09-29: Poster ingests only on the maintainer's Pull. See "Manual Poster ingest" below. Clipper clause amended 2026-10-03: Clipper sends only on the maintainer's Send click. See "Manual Clipper send" below.)* |
| Q13 | Consolidation depth | Move-and-wire only. Behavior changes are limited to those ratified here (front door, auto-transport, single config, configurable data root). Deduplicating transcription, Slate CSS, and Anthropic clients, and any model changes, are post-burn-in Issues. Dependency alignment needed for one venv (fact 1) is in scope. |
| Q14 | Config | One `ricesuite.env` at the suite root, preserving every existing variable name, a single `ANTHROPIC_API_KEY`. The launcher sets both handoff-directory variables itself so they cannot be mismatched. |
| Q15 | Agent rules | Root `CLAUDE.md` with suite hard rules (never auto-post; only `poster/` touches posting; local-only; merge/release/deploy/visibility reserved to the maintainer). Each pillar directory keeps its own `CLAUDE.md`. An automated **boundary test** fails CI if `searcher/` or `clipper/` imports Playwright or any `poster` module. Same Issue → branch → draft PR → three cold reviewers workflow. Poster's gitignored `SPEC.md`/`CLAUDE.md` notes stay local and gitignored. |
| Q16 | Quality gates | One CI keeps every pillar's current gates and coverage floor (90 / 85 / 43), none weakened, no averaged floor. Add suite-level tests for launcher, gateway, and auto-transport, plus one **full-chain mock-mode test** (local file → select → render → Poster draft; `POST_MODE=mock`; no network, no posting). |
| Q17 | Crash / stop | Launcher auto-restarts a crashed pillar. A Poster post in flight at crash time is recorded **unconfirmed** and never auto-retried. `rice stop` refuses while a post is running unless `--force`, and warns when a scheduled batch is due. |
| Q18 | Background running | v1 runs in the foreground only while open (as today). An optional login service is a post-burn-in Issue. |
| Q19 | Repo visibility | Private during burn-in. Going public, simultaneously with archiving the old repos, is a maintainer-only action. *(Closed 2026-10-05: public since 2026-09-26. See "Burn-in complete; public" below.)* |

## Consequences

**Easier:** one command and one tab for the daily workflow; no Pull/Send
plumbing clicks or mismatched handoff-dir settings; one repo, one venv, one CI.
*(Since the amendments of 2026-09-29 and 2026-10-03, Poster's Pull and
Clipper's Send are clicks again. See "Manual Poster ingest" and "Manual
Clipper send" below.)*

**Harder / accepted:** a launcher and gateway are new moving parts; three
processes cost more memory than one; duplicated internals (two Whisper
wrappers, three Slate copies) persist until post-burn-in cleanup; during
burn-in, critical fixes land twice.

## Open items this decision depends on

*(All closed 2026-10-05. See "Burn-in complete; public" below.)*

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

## Post-burn-in amendment — 2026-09-29

**Status: ratified; implemented.** #6, #5/#7 and #10 merged on 2026-09-29
(PRs #31, #33, #32). The maintainer ran the live migration to `~/.ricesuite`
the same day, confirmed real sessions from the new location, and approved
removal of the verified originals. This amendment releases the Q10
and Q13 follow-ups in RiceSuite Issues [#5](https://github.com/gidde032/RiceSuite/issues/5),
[#6](https://github.com/gidde032/RiceSuite/issues/6),
[#7](https://github.com/gidde032/RiceSuite/issues/7), and
[#10](https://github.com/gidde032/RiceSuite/issues/10).

The maintainer reports several days of successful real use and explicitly
accepts that trial as satisfying the burn-in prerequisite **for these four
changes**. This is maintainer acceptance, not an independently audited claim
that every numerical Q9 criterion was measured. It does not authorize original
repository archival, publication, or other release actions.

The final decision challenge settled these choices in one round (two
questions):

- Deliver #5 and #7 together as shared Python infrastructure, #6 separately
  as shared Slate styling, and #10 separately as data migration. Preserve
  transcript contracts, prompts, model selections, and each page's appearance.
  Separate pillar processes and all three human gates remain unchanged.
- Unify the existing application data covered by Q10, **including browser
  profiles**, under `~/.ricesuite` by default, with a configurable alternative.
  The maintainer selected complete migration over leaving profiles behind,
  and hidden application storage over a visible `~/RiceSuiteData` folder.
- Migration is explicitly maintainer-run: copy and verify before switching,
  retain the originals, and provide a rollback procedure. No automatic move on
  ordinary startup. Existing installations must not silently appear empty
  because a default changed. A retained pre-migration copy alone does not
  guarantee lossless rollback after new activity; the implementation must
  explain and enforce that distinction.
- User documentation must explain how to find the data in macOS Finder and
  Windows File Explorer, including hidden-folder access and custom locations.
  Windows browsing instructions do not expand the existing macOS/Linux
  application-support contract.

The material assumption exposed was that unifying paths includes changing
Chrome's profile location. Fixture verification cannot establish real-account
session continuity. Implementers must document that remaining maintainer-only
validation; agents may neither inspect live data nor open real profiles under
this approval. No research prerequisite remains for preparing the work.

The maintainer authorizes the implementation sessions to implement, verify,
run independent subagent reviews, repair validated in-scope findings, and
create/update draft PRs through the agentic workflow. Routine in-scope repair
does not require another approval round. Marking ready, merging, releases,
deployment, visibility changes, live migration, and real-profile access remain
outside that authority. The planning session writes the handoff prompts only;
it does not implement these changes.

## Quality-gate adjustment — 2026-09-29

The maintainer explicitly raised Poster's six-test smoke-tier execution ceiling
from 1.5 to 2 seconds after the RiceSuite #33 CI runner measured 1.568 seconds
on one run and passed on rerun. The gate continues to read pytest's JUnit
execution time, excluding interpreter startup and collection. Its exact test
inventory, separate 10-second wall-clock hang detector, and Poster 43% coverage
floor remain in force. This is a narrow, approved adjustment to Q16's initial
gate-preservation decision; future gate changes still need maintainer sign-off.

## Manual Poster ingest — 2026-09-29

**Status: ratified by the maintainer** (RiceSuite Issue
[#30](https://github.com/gidde032/RiceSuite/issues/30), agentic decision
challenge, 2026-09-29). This amends the Poster clause of Q12 and, for Poster,
Q7's "each consumer auto-ingests". The Q7 and Q12 rows above are unchanged
except for a pointer here.

- **Poster never ingests a Clipper batch on its own.** Only the maintainer's
  **Pull from Clipper** click pulls, whether the draft workspace is empty or
  not. The page does not pull when it opens, when its slots are empty, or after
  a run. Pull still confirms before overwriting unposted drafts (fact 6), and
  captions still generate on ingest.
- **Waiting batches stay visible.** Post's inbox bar keeps reporting waiting,
  unacknowledged and errored batches, and points to Pull from Clipper. The
  Pull button shows how many batches wait. The home view keeps its count.
- **Unchanged:** Searcher → Clipper and Clipper → Poster transport (Clipper
  still auto-sends a fully rendered batch to the handoff; superseded
  2026-10-03 by "Manual Clipper send" below), the handoff
  contracts, and all three human gates (Q3). Batches wait durably in the
  handoff folder until pulled.
- The automatic path's no-replay request mode (`replay=0`) is removed: every
  Pull recovers an unacknowledged batch first, as the button always did.

**Reason:** the maintainer wants to decide when a new batch lands in Review,
not have the page fill an empty workspace on its own (for example straight
after New Run).

The same decision added **Restore last batch** to Poster's Review page, which
is pillar-internal behaviour recorded in Issue #30 and Poster's changelog: it
only fills drafts, never posts or schedules, and confirms before overwriting
unposted drafts.

## Manual Clipper send — 2026-10-03

**Status: ratified by the maintainer** (RiceSuite Issue
[#59](https://github.com/gidde032/RiceSuite/issues/59), 2026-10-03). This
amends the Clipper clause of Q12. It also replaces the parenthesis "Clipper
still auto-sends a fully rendered batch to the handoff" in "Manual Poster
ingest" above. The Q12 row is unchanged except for a pointer here.

- **Clipper never sends a batch on its own.** Only the maintainer's **Send to
  RicePoster** click sends. A finished Render all, a clip removal, or any
  other event does not send.
- **The send rules are unchanged.** Send refuses a batch with an unrendered,
  failed, or edited clip, and names those clips. It never sends part of a
  batch. A second send of a sent batch asks first.
- **Render all reports, then stops.** After a successful Render all, the
  progress bar stays on Render all and reads "Send the batch to Poster when
  ready."
- **Unchanged:** Searcher → Clipper transport, the handoff contracts, and all
  three human gates (Q3). Clipper pulls the next Searcher batch only when
  nothing unsent would be displaced, so a rendered batch holds the workspace
  until it is sent or discarded.

**Reason:** the maintainer wants to decide when a batch leaves Clipper, as
"Manual Poster ingest" lets them decide when it lands in Poster.

## Burn-in complete; public — 2026-10-05

**Status: ratified by the maintainer** (2026-10-05, with PR
[#60](https://github.com/gidde032/RiceSuite/pull/60)). This closes Q9, Q19,
and the open items above. The Q9 and Q19 rows are unchanged except for a
pointer here.

- **Burn-in passed.** The maintainer declares the Q9 burn-in complete.
  RiceSuite is the supported app for daily use. Q9's "critical fixes only"
  rule for the old repos ends.
- **The repository is public** since 2026-09-26.
- **The original repositories are superseded.** `gidde032/RiceSearcher`,
  `gidde032/RiceClipper`, and `gidde032/RicePoster` get no fixes. All work,
  Issues, and pull requests go to RiceSuite. Their deprecation PRs, their
  archiving, and any Issue transfer (Q4) stay maintainer-only actions (root
  `CLAUDE.md` rule 4).
- **Open items.** Item 1, the fingerprint parity probe, no longer gates
  anything, because RiceSuite is now the only app in use. Item 2's Issues are
  resolved: #5, #6, #7, and #10 are delivered, #4 is closed as not wanted, and
  #8 is settled by model settings in `ricesuite.env`. Only #9, the desktop
  wrapper, stays open.
- **Unchanged:** the launcher still refuses to start while anything answers
  on the old apps' ports (8765 / 8000 / 1738). The guard now protects against
  a legacy app started by mistake. All three human gates (Q3) and the suite
  hard rules stay.
