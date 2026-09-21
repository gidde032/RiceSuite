# Security & Privacy

RiceClipper is a **local-first** tool. It runs on your machine, reads local
video files, and writes local output files. It does **not** post, publish, or
upload content anywhere. This document explains what leaves your machine (and
what never does), how to handle secrets, and how to report a problem.

## What data leaves your machine

Under normal use, **nothing** leaves your machine. Transcription
(faster-whisper), subject detection (OpenCV YuNet), and rendering (ffmpeg +
libass) all run locally. The Whisper model is downloaded once on first run from
its model host and then cached locally.

The **only** outbound network call in the entire design is the optional
on-screen header generator (SPEC §6.2), and it is **opt-in**:

- It is **never** triggered automatically. Nothing is sent after transcription
  on its own — you invoke it explicitly with the header **"✨ Generate"** button
  in the review UI.
- It requires an `ANTHROPIC_API_KEY`. With no key set, the feature is inert and
  the UI simply keeps your manually typed header.
- When you do trigger it, RiceClipper sends the following to the Anthropic
  Messages API for that single request:
  - one **early frame** of the clip, as a JPEG (used as a visual signal for the
    hook; a music-only clip with no transcript relies on this frame),
  - the **reviewed transcript text** of the clip,
  - any optional **editor note / feedback / "avoid" guidance** you typed, and
  - the selected **header-style system prompt** (from `prompts/`).
- It generates **text only** and posts nothing. On any failure the UI keeps
  your manual header, so a render is never blocked.

If you do not want any clip frame or transcript to ever reach a third party, do
not set `ANTHROPIC_API_KEY` and do not use the header generator — every other
feature works fully offline.

## API keys & secrets

- Provide `ANTHROPIC_API_KEY` via your environment (e.g. an untracked local
  `.env`, which is gitignored). See [`.env.example`](./.env.example).
- **Never commit** API keys, `.env` files, credentials, session tokens,
  databases, or certificates. `.gitignore` is configured to keep these out, but
  review your staged changes before committing.
- Maintainer-specific header prompt styles can name real people or accounts and
  are kept local: only the neutral `prompts/generic-header.json` seed is tracked
  (`prompts/*.json` is otherwise gitignored).

## Network exposure

- The review server binds to localhost and is intended to be run only on your
  own machine (`uvicorn app.main:app`). Do **not** expose it to untrusted
  networks or the public internet; it has no authentication and is not hardened
  as a public web service.
- Uploaded sources, intermediates, and rendered outputs live under the local,
  gitignored `.riceclipper_work/` cache until you clear them from the UI.

## Handling private media

- Input videos and rendered clips are treated as private local files and are
  never uploaded (aside from the single opt-in header frame described above).
- The RicePoster / RiceSearcher handoff directories are **local filesystem**
  paths on your own machine (see [`docs/integration/`](./docs/integration/));
  no network transfer is involved.

## Reporting a vulnerability

If you believe you have found a security or privacy issue, please report it
privately to the maintainer rather than opening a public issue:

- Email: **finn.gidden@gmail.com** (subject line prefixed `RiceClipper security`)

Please include a description, reproduction steps, and the affected version or
commit. You will receive an acknowledgement, and fixes will be prioritized
according to severity.
