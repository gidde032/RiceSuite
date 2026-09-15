# Saved profiles — design spec

Status: **RATIFIED 2026-09-14 (ADR-002 ACCEPTED). Implementation authority comes from the owning GitHub Issue.**
Date: 2026-09-14. Companion: `ADR-002.md`, `~/fable-scan/slotE/handoff-contract.md` (outside the repo).

## Purpose

Let the maintainer keep several named content types, each with its own scoring
prompt, in one library. Scoring, review, dedup, and handoff work inside one
profile at a time. Sources and transcripts are shared.

## Consumers

- The maintainer, through the CLI and the review UI.
- RiceClipper pickup. It reads the manifest and keeps the clip object.
- RicePoster. No change. It never sees the profile.

## Contract

### Profile file

- Location: `<data_dir>/profiles/<profile_id>.json`. Env `RICESEARCHER_PROFILES_DIR` overrides the directory.
- `profile_id` is the file stem. Pattern `^[a-z0-9][a-z0-9-]{0,39}$`. The loader rejects other names.
- Fields: `version`, `name`, `brief` (required); `keywords`, `positive_examples`, `negative_examples` (optional). Same as today.
- `BeatProfile` gains `id: str`. `load_profile(profile_id)` replaces the path and env forms. `RICESEARCHER_BEAT_PROFILE` is retired.
- `list_profiles()` returns every valid file, sorted by id. A malformed file is reported and skipped.
- Seed: if the directory has no files, copy the packaged `default.json` to `example-beat.json` once.

### Library (schema v3)

- Add `profile_id TEXT NOT NULL DEFAULT ''` to `candidate_slices`.
- Migration: set `profile_id = 'example-beat'` on every row. Rewrite `id` from `{source}:{in}-{out}` to `{source}:example-beat:{in}-{out}`. Rewrite `dup_of` the same way. Add index `(profile_id, status)`.
- The legacy id is a constant `LEGACY_PROFILE_ID = "example-beat"` in `store.py`.
- `replace_candidate_slices(source_id, profile_id, slices)` deletes candidate rows for that source **and** profile only.
- `list_slices(profile_id=..., source_id=..., status=...)`. `profile_id` is required by every caller in the CLI and web app.
- `profile_counts()` returns, per profile id: distinct sources, candidates, selected, handed_off.
- `delete_source` and `delete_all_sources` cascade across all profiles. FR-8a is unchanged.

### Pipeline

- `_slice_id(source_id, profile_id, target_in, target_out)`.
- `extract_and_score` stamps `profile_id = profile.id` and `beat_profile_version = profile.version`.
- The protected set is computed inside the same profile. Rows of other profiles are never read or written.
- `annotate_library_duplicates(profile_id)` compares slices inside one profile only.

### CLI

- `score --profile ID` — required. No default.
- `slices --profile ID`, `dedup --profile ID`, `handoff --profile ID` — required.
- `profiles` — one line per profile: id, name, version, sources, candidates, selected.
- `pull`, `list`, `show`, `review` — unchanged.

### Web API

- `GET /api/profiles` → `[{id, name, version, sources, candidates, selected, handed_off}]`.
- `GET /api/slices?profile=ID&status=` — `profile` required; 400 without it. Each DTO gains `profile_id` and `stale: bool` (version differs from the file).
- `POST /api/handoff` body `{"profile": ID}` — hands off `selected` rows of that profile only.
- Status and window endpoints are unchanged. They act on one slice id.
- `/api/sources` gains nothing in this phase.

### Web UI

- Topbar: a `Profile` select before the status filter. The choice persists in `localStorage`. Every load passes `profile=`.
- The handoff button label names the profile: `Send 3 selected (example-beat) → RiceClipper`.
- New page `/profiles`, nav link `Profiles`. One row per profile: name, id, version, sources, candidates, selected. Click a row: set the active profile and go to `/`.
- A stale slice shows a `stale` badge with the stored version in its title.
- Slate tokens only. Match `../RiceClipper/docs/design/slate-ui-spec.md`.

### Handoff manifest

- Each clip gains `"profile_id": "<id>"`. `schema_version` stays `1`. The change is additive.
- `HandoffEntry` gains `profile_id`. `hand_off_selected(profile_id)` selects inside the profile.

## Boundaries

- No profile create, edit, or delete in the UI. Files only.
- No auto-select. The human gate stays (hard rule 4).
- No rescore on migration. No cross-profile dedup.
- No per-profile prefilter weights. No SPEC.md edit by the agent.
- No change to RiceClipper or RicePoster code.

## First reliability risk

A partition leak. A re-score, dedup pass, or handoff under profile A touches
rows of profile B. The regression test: score one source under two profiles,
then re-score B. Assert every A row is byte-identical. Select one slice in
each. Hand off A. Assert the batch holds one clip and B stays `selected`.

## Build order for a cheap model

1. **P1 loader.** Profiles directory, id rule, seed copy, `list_profiles`. Tests.
2. **P2 store.** Schema v3 migration from a committed v2 fixture DB. Filters. Counts.
3. **P3 pipeline + CLI.** Slice id, stamping, protected set, `--profile` flags, `profiles` command.
4. **P4 handoff.** `profile_id` on entry and manifest. Writer test asserts the key.
5. **P5 API + UI.** Endpoints, select, page, stale badge, handoff label.

P1 to P4 are one PR. P5 is a second PR. Each PR maps to its Issue.

## Gates the PRs must pass

- `ruff format --check`, `ruff check`, type-check.
- `pytest` with coverage at or above the current floor (90%). Smoke tier count unchanged or updated in the same PR.
- Migration test: a v2 fixture DB upgrades; ids, `dup_of`, and statuses are preserved.
- Partition regression above, fail-before-fix.
- Manifest test: `profile_id` present; `schema_version == 1`.
- Frontend: `node --check` on both JS files; review and profiles pages checked at 1440 and 390 px wide.
- Cold review by three reviewers before ready.
