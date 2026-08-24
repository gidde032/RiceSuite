# Spike: color-emoji burn-in

**Status:** OPEN
**Priority:** blocks Wave-1 auto-header; also affects the v1 manual header
**Owner:** _unassigned_
**Estimate:** ~30 minutes

## Question

When a header contains color emoji (e.g. 🥹, 😂), does our ffmpeg/libass
toolchain burn them into the video **in color and correctly positioned** — or does
it render them as monochrome glyphs or empty "tofu" boxes?

## Why it matters

- The header is the single most visible line of text on the clip.
- Both real header examples use color emoji ("… anniversary pics 🥹", "… too funny
  😂").
- The Wave-1 auto-header uses a Sonnet agent that will readily emit emoji, so if
  burn-in is broken, **every auto-generated header looks broken**.
- Even in v1, the manual header can contain emoji — so this is worth confirming
  early, not only before Wave 1.
- libass is known to render color emoji inconsistently: without an available
  color-emoji font it commonly falls back to monochrome or missing-glyph boxes.

## Pass / fail criteria

**Pass:** a rendered test clip shows the emoji in the header **in full color**,
at the correct size and position, with no boxes, no monochrome fallback, and no
layout breakage on the surrounding text.

**Fail:** emoji appear as boxes, as black-and-white glyphs, mis-sized, or shove
the header text out of position.

## Suggested method

1. Build a minimal header string with mixed text + color emoji (use the two real
   examples).
2. Render it through the intended ASS → ffmpeg/libass path onto a test clip.
3. Ensure a color-emoji font (e.g. Noto Color Emoji) is present and reachable by
   libass; note what had to be installed/configured.
4. Inspect an output frame at full resolution.

## Fallback if libass can't do color emoji cleanly

Pre-render the header (text + emoji) to a transparent PNG using a renderer that
handles color emoji, then overlay that image via ffmpeg instead of drawing the
header through libass. Heavier, but reliable. Record which path we chose and why.

## Result

_To be filled in when the spike runs._

- Toolchain / font configuration used:
- Outcome (pass/fail):
- Path chosen for the header (libass direct vs. image overlay):
- Notes:
