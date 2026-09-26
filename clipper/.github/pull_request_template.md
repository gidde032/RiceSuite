## Summary

<!-- What changed, and why is this the smallest coherent change? -->

## Linked issue

<!-- Use "Closes #123" only when this PR fully resolves the issue. -->

## Scope

### Included

-

### Explicitly not included

-

## Verification

<!-- List exact commands and measured results. -->

- [ ] Targeted tests:
- [ ] `python -m pytest tests/ -q`
- [ ] `python -m pytest tests/ --cov=app --cov=render --cov=transcribe --cov-fail-under=85 -q`
- [ ] `python -m ruff check . && python -m ruff format --check .`
- [ ] Additional manual or offline verification:

## Boundary and safety

<!-- SPEC.md §3: RiceClipper reads and writes local files only. It performs no
     posting, publishing, or content upload. The single outbound network call in
     the whole design is the deferred Wave-1 header agent, which generates text
     and posts nothing. -->

- [ ] No posting, publishing, or content upload was added or triggered.
- [ ] No outbound network call was added (or, if the deferred header agent, it
      remains text-only and posts nothing — note it below).
- [ ] Change stays within the active phase (no unapproved roll into Wave-1 scope).

Notes:

## Documentation impact

<!-- CLAUDE.md rule 4: SPEC.md / ROADMAP.md / CHANGELOG.md are first-class. -->

- Updated:
- Reviewed; no change needed:
- Verification:

## Review accounting

<!-- Record accepted, deferred, and rejected findings with rationale. -->

- Accepted:
- Deferred:
- Rejected:

## Release note

<!-- One user-facing sentence, or "Not user-facing." -->
