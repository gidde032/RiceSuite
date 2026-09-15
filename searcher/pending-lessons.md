# Pending lessons

Temporary, evidence-backed lessons awaiting promotion to a practice document or an
Issue. Each entry: what happened, the evidence, and the proposed durable home.
Empty at bootstrap — entries earn their place through real work.

- **2026-09-14 — Local green, CI red: a committed fixture was gitignored.** PR #24's
  v2 fixture DB matched `*.sqlite3` in `.gitignore`, so the implementer's "gates
  green" report was local only; CI failed with FileNotFoundError. Evidence: run
  34904896914, fix 3c3e8c5. Proposed home: practices catalogue — "an implementer
  report must include a CI check URL, not only local gate output"; and a
  verification rule "any new test fixture: `git ls-files <path>` before push".
- **2026-09-15 — Fail-before-fix must fail for the named risk.** PR #24's partition
  regression first went red on a missing column, not on the leak. Reviewer C caught
  it; split test proved the leak at bfb1940. Proposed home: verification discipline
  — "state which assertion is the red one and why it maps to the risk".
