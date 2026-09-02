<!-- Link the owning Issue. Use "Closes #N" only if merging fully completes it. -->
Issue: #

## What & why

## Verification
- [ ] `ruff format --check .`
- [ ] `ruff check .`
- [ ] type-check
- [ ] `pytest` + coverage floor
- [ ] smoke tier green (count matches inventory)

## Safety
- [ ] No code path posts, publishes, or uploads content.
- [ ] Local-first; no secrets or rights-risky content in tracked files.

## Docs impact
- [ ] SPEC/ADR/ROADMAP/handoff reconciled as needed.
