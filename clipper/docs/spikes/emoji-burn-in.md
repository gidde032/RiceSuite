# Spike: color-emoji burn-in

**Status:** PASS — via the PNG-overlay fallback (built and verified by
rendered-frame inspection). libass-direct cannot render color emoji on this
macOS/CoreText toolchain; emoji headers are now composited as an image instead.
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

**First pass — 2026-08-24**

- **Toolchain:** `homebrew-ffmpeg/ffmpeg/ffmpeg` build, **libass 0.17.5**
  (linked), rendering the header through the `subtitles` filter (libass direct).
- **Fonts available to fontconfig:** only **Apple Color Emoji.ttc**. This font is
  in Apple's **`sbix`** bitmap format. **Noto Color Emoji is NOT installed.**
- **Outcome:** **FAIL** — emoji render as empty "tofu" boxes.
- **Diagnosis:** libass 0.17.x can render color glyphs in the **`CBDT/CBLC`** and
  **`COLR/CPAL`** formats, but **not** Apple's `sbix` bitmap format. With Apple
  Color Emoji as the only color-emoji font, libass finds no usable color glyph
  and falls back to the missing-glyph box. This is a **font-format** problem, not
  a libass-is-broken problem.
**Second pass — 2026-08-24.** Installed Noto Color Emoji
(`brew install --cask font-noto-color-emoji`) → emoji **still** rendered as boxes.

Diagnosed the real cause with `fc-match`:

```
fc-match ":charset=1f602"   → .LastResort   (placeholder-box font!)
fc-match "Noto Color Emoji:charset=1f602"  → Noto Color Emoji   (works by name)
```

On macOS + Homebrew fontconfig, Apple's `.LastResort` font claims **every**
Unicode codepoint and outranks real color-emoji fonts in generic fallback. libass
does generic fallback via fontconfig, gets `.LastResort`, and burns its empty
boxes. Confirmed the fix by rejecting `.LastResort`:

```
# with .LastResort rejected:
fc-match ":charset=1f602"   → Noto Color Emoji   ✅
```

**Third pass — 2026-08-24 (rendered-frame inspection, definitive).**

The fontconfig approach was tried and **removed** — it does nothing on macOS.
Rendering an emoji header through the actual `subtitles` filter and inspecting the
output frame showed:

- Text renders perfectly; both emoji render as empty **boxes**.
- ffmpeg logs `CoreText note: Client requested name ".LastResort"` — i.e. **this
  libass build uses the macOS CoreText backend, not fontconfig.** So
  `FONTCONFIG_FILE` / any `fontconfig.conf` is ignored (with-conf and no-conf
  frames were byte-identical).
- An explicit inline `{\fnApple Color Emoji}` override in the ASS also failed —
  still boxes. libass's own fallback resolves emoji codepoints to `.LastResort`.

**Conclusion:** libass-direct color-emoji burn-in is **not achievable** on this
macOS/CoreText toolchain by any ASS/font/fontconfig means.

**Chosen path (to build): PNG-overlay fallback.** Render the header (text +
color emoji) to a transparent PNG with a color-capable renderer, then `overlay`
it via ffmpeg; captions stay on libass (they carry no emoji).

**Built — 2026-08-24.** Path chosen: **PNG overlay**, implemented in
`render/header_image.py` + wired into `render/pipeline.py`.

How it works:
- `has_emoji(header)` gates it. **Text-only headers stay on the libass path
  unchanged**; only headers containing emoji use the overlay.
- `render_header_png()` (Pillow) draws the header text + color emoji on the
  legibility plate to a full-frame transparent PNG; the pipeline adds it as an
  ffmpeg input and composites with a single `overlay=0:0` after the `subtitles`
  filter. Captions always stay on libass.
- If image rendering fails, the pipeline **degrades to the libass header** rather
  than failing the render.

Font finding (the fiddly part, resolved):
- The Homebrew **Noto Color Emoji is COLRv1/vector and rasterizes BLANK in
  Pillow** at every size — do not use it here.
- **Apple Color Emoji (sbix) renders correctly in Pillow** at its valid strikes
  (64/96/**160**); 109/128/136 raise "invalid pixel size". `_resolve_emoji_font()`
  auto-probes fonts/strikes and picks the first that yields non-empty pixels
  (Apple @160 on this Mac), then scales glyphs to the text size.
- Pillow has no font fallback, so mixed strings are segmented into text/emoji runs
  and drawn sequentially; greedy word-wrap handles 1-2 line hooks.

**Verified:** rendered a clip with header "anniversary pics 🥹 too funny 😂" +
captions — emoji burn in full color, on the plate, persisting the whole clip,
with word-highlight captions intact. Linux note: a fontconfig-backed libass with
Noto + rejecting `.LastResort` would also work libass-direct, but the overlay is
the portable answer that covers macOS.
