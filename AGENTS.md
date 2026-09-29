# RiceSuite — Agent Instructions

[`CLAUDE.md`](CLAUDE.md) is the operating contract for every agent working in
this repository, whatever the tool. Read it, and the ADR and specs it names,
before acting. Its suite hard rules apply in full here. Each pillar directory's
own `CLAUDE.md` applies inside that pillar.

The rule below is repeated here because breaking it harms the maintainer's
real accounts:

**Real Instagram profiles are a limited resource.** Opening any real Instagram
profile (a Chrome profile under Poster `sessions/instagram/`) requires the
maintainer's explicit sign-off, even when nothing will be posted: a login,
session status or health check, fingerprint probe, layout check, or debugging
run all count. Sign-off covers the one occasion it was given for and does not
carry over. Once opened, a profile does its task and closes. Never loop, poll,
retry-launch, or repeatedly open and close a profile with no activity, because
that pattern gets accounts flagged and is never acceptable. Verify with fakes,
fixtures, and throwaway temporary profiles instead; if a task seems to need a
real profile, stop and ask.
