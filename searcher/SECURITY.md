# Security & responsible-use notes

RiceSearcher is a **local-first** content-sourcing tool. It runs on your own
machine, reads source media, and writes local files. It has a hard, code-level
safety boundary and some responsible-use expectations for anyone running it.

## Safety boundary (enforced in code)

- **Never posts, publishes, or uploads content.** No code path contacts any
  posting, publishing, or upload surface (see `CLAUDE.md`, `SPEC.md` FR-10). The
  only outbound network calls are:
  1. **Source acquisition** — `yt-dlp` fetch, only when *you* run
     `ricesearcher pull <url>` with a URL you supply. The local watch-folder door
     (`ricesearcher pull ./file`) makes zero network calls.
  2. **Scoring** — the Anthropic API, only when *you* run `ricesearcher score`.
  Neither call authenticates to, or acts on, any account, platform, or posting
  endpoint. There is no background watcher, scheduler, or automatic outbound
  behavior.

## Secrets

- The only credential the tool uses is `ANTHROPIC_API_KEY` (scoring only).
- Provide it via an exported environment variable or a local, **gitignored**
  `credentials.env` (copy `credentials.env.example`). **Never commit real keys.**
- `.gitignore` blocks `credentials.env`, all `*.env` (except `*.env.example`),
  and common key/cert/session/cookie files. If you add new secret-bearing files,
  extend `.gitignore` first and verify with `git status` before committing.

## Local review server

- `ricesearcher review` serves the Slate review UI on **`127.0.0.1:8765`**
  (localhost only). The media-management endpoints (per-source delete, cache
  clear) are intentionally **unguarded at the API layer**; the destructive UI
  controls are gated by a two-step confirm. **Do not expose the review server on
  a shared or public interface** — keep it bound to `127.0.0.1`.

## Rights & source material

- Source material is typically **copyrighted**. Every candidate slice tracks a
  `rights_risk` field. This tool is for local discovery, review, and handoff; it
  keeps content **off any public surface**. You are responsible for how you use
  acquired material and for complying with the terms of service of any source you
  pull from.

## Reporting a vulnerability

If you find a security issue (for example, a code path that could exfiltrate
data, leak a secret, or reach a network endpoint outside the two documented
above), please **open a private report** rather than a public issue: use GitHub's
"Report a vulnerability" (Security advisories) on this repository, or contact the
maintainer directly. Please do not open a public issue for a suspected
vulnerability until it has been addressed.
