# RiceSuite — Operating Rules

RiceSuite is one local app built from three pillars: `searcher/` (RiceSearcher),
`clipper/` (RiceClipper), and `poster/` (RicePoster). The suite contract is
[`docs/adr/ADR-001-ricesuite-consolidation.md`](docs/adr/ADR-001-ricesuite-consolidation.md)
(Q1–Q19, ratified). Read it before acting and do not reopen it.

## Suite hard rules (non-negotiable, ADR-001 Q3/Q15)

1. **Never auto-post.** Transport between pillars may be automated. The three
   human judgement gates may not: Searcher select, Clipper review/render, and
   Poster Post All / Schedule. No code path may post, schedule, or discard
   Poster drafts without that human action.
2. **Only `poster/` touches posting.** `searcher/` and `clipper/` must never
   import Playwright or any `poster` module, and must have no code path to a
   posting surface. An automated boundary test enforces this in CI.
3. **Local-only.** The suite binds to localhost. Hosted, cloud, or LAN
   deployment is excluded, because Poster's API is unauthenticated and can post
   to real accounts.
4. **Maintainer-only actions.** Merging, marking PRs ready, tags, releases,
   deployment, repository visibility, and archiving or transferring anything in
   the original repositories are reserved to the maintainer.
5. **Protect live data.** Tests and agent smoke runs use temporary directories
   only. Never read, write, move, or delete the live Searcher library,
   either handoff directory, Poster `sessions/`, queue, history, media, or
   credentials, or Clipper's work directory. Run Poster with `POST_MODE=mock`
   in any agent session.
6. **Never weaken a gate.** Each pillar keeps its own gates and coverage floor
   (Searcher 90, Clipper 85, Poster 43). There is no averaged floor.

## Source-of-truth order

1. Code and tests.
2. ADR-001 for suite-level structure; the suite `SPEC.md` once written.
3. Each pillar's own `SPEC.md` / ADRs for pillar-internal behavior, unchanged
   except where ADR-001 overrides them (Q12 transport, Q14 config, Q10 Poster
   data root).

Each pillar directory keeps its own `CLAUDE.md` for pillar-internal rules.
Poster's `SPEC.md` / `CLAUDE.md` notes stay local and gitignored, as in the
original repository.

## Workflow

Issue → focused branch → draft PR after the first coherent green commit →
three cold reviewers (a correctness reviewer, an ADR/contract reviewer, and a
standing skeptic) → every accepted finding repaired with a fail-before-fix
regression test or deferred to a linked Issue → maintainer merges.

`#N` references inside `searcher/`, `clipper/`, and `poster/` (and in imported
commit history) point to the original pillar repositories.
