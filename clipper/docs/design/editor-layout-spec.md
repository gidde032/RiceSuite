# Editing page layout

Status: **Ratified design contract, 2026-09-23. Delivery tracked in Issue #6.**

The [interactive reference](editor-layout-reference.html) is the approved
Balanced layout. Its Music and Speech switches show the two required review
states. The [exploration file](proposals/editor-layout-options.html) records the
three options considered; it is not an implementation reference. This document
owns editing-page geometry and spacing. The [Slate UI spec](slate-ui-spec.md)
continues to own the existing palette, logo, visual treatments, and behavior,
except where this document explicitly amends them.

## Outcome and scope

Make each clip editor use the width of a browser viewport and give text review
the largest useful work area. Keep upload, transcription, per-word correction,
lyric alignment, visual-style values, rendering, and RicePoster handoff behavior
unchanged. This is a layout and presentation redesign of the review state, not
an edit to the media pipeline.

## Desktop anatomy

At viewport widths of **881 px and above**, the editor has two rows:

1. **Upper row:** video preview and its existing geometry/source notes on the
   left; all existing editing and caption settings on the right. Target a
   **40/60** preview-to-settings split. Keep the video near its current useful
   size, with a maximum displayed height of **360 px**; expanding text is the
   priority when more screen space becomes available. Condense Content,
   Geometry, Header style, Burn captions, Caption style, and optional Music
   controls into short, consistently spaced rows. Keep Header and Generate in
   the settings area.
2. **Lower row, Music:** generated transcript on the left and optional pasted
   lyrics on the right, in **equal-width, equal-height** panes. Both begin on
   the same horizontal line. The pasted-lyrics textarea fills its pane; Align,
   Restore transcript, and alignment status sit directly below it. Transcript
   and lyric text use identical reading styles.
3. **Lower row, Speech:** a single generated transcript pane spans the **full
   width** of the lower row. No lyric pane or replacement control panel occupies
   that row. Header, content, geometry, header style, burn-captions, caption
   style, and optional music settings remain in exactly the same upper-right
   location and order as Music mode.

The two text states share one transcript component and the same settings
component. Changing Content changes only the lower-row layout and applicable
lyric actions. The transcript remains editable word by word with timing locked.
The bottom batch-action bar stays reachable without covering editable content.
Multiple clips remain sequential cards.

## Spacing and typography contract

The review page defines **one spacing unit**, `--editor-space: 8px`, at its
root. Page inset, clip inset, section separation, row gaps, label-to-control
gaps, pane gap, and action-bar gap are derived from this unit using `calc()`
with **0.5×, 1×, or 2×** multipliers. Do not introduce isolated pixel margins
or padding on a new review control. Use **16 px** (2×) page inset on wide
viewports and **8 px** (1×) below the breakpoint. Remove the current 1540 px
review-page width cap: the review card should grow to the available browser
width, while retaining the page inset on both sides.

Use `Arial, sans-serif` for transcript text, pasted lyrics, geometry/source
notes, alignment status, and other small grey operational text that currently
uses a monospace stack. Transcript and lyrics share a single text style:
**14 px** font, **1.5** line height, identical padding, border, background,
and minimum height of **280 px**. Their height may grow with viewport height
up to **440 px**, and both panes must always have the same computed height in
Music mode. Use normal letter spacing for reading text. Uppercase control
labels retain the Slate **12 px** size and use one shared **0.02em**
letter-spacing value; other interface text uses normal letter spacing. Keep the
existing Slate **15 px** body scale. Caption preset samples, including the actual
**Mono** preset sample, keep their established treatment fonts because they
preview rendered output.

Choice cards, row padding, and ordinary action buttons become shorter than in
the current editor, while every interactive target remains at least **36 px**
high for fine pointers and **44 px** for coarse pointers. Do not reduce text
size to fit controls; wrap caption choices into more rows when needed.

## Color amendment

Use **`#8B0000`** as a solid fill with **white text** for these three controls
only:

- the `face near header` warning badge;
- each clip's remove `×` button; and
- the batch `Start over` button.

The warning remains text-labeled. Keep visible focus indication. The Slate
palette, other status/error colors, logo, and caption preset colors remain as
already specified.

## Responsive layout

At **880 px and below**, each clip becomes a single readable column in this
order: video and notes, settings, transcript, then pasted lyrics when Music is
selected. Speech retains the same settings position and omits the lyric pane.
The transcript and lyric textarea use the full available column width and the
same text style. The page and controls must not overflow horizontally or hide
focus outlines. The batch actions may stack on narrow screens and must not
cover either text pane.

## Required verification for implementation

Automated browser checks must use **390×844, 768×1024, 1024×768, 1440×900,
and 1920×1080** viewports, in both Content modes. Pin computed geometry and
styles rather than relying on a screenshot alone:

- page inset derives from `--editor-space` at every viewport; no horizontal
  document overflow;
- desktop upper row has preview left and settings right, and narrow order is
  video → settings → transcript → lyrics where applicable;
- Music transcript and lyric panes have equal computed width and height and
  aligned top edges; Speech transcript spans the full lower row;
- settings remain in the same location and order across modes;
- transcript and lyrics use identical computed Arial font, line height,
  padding, border, and background; small grey operational text also uses Arial;
- warning, remove, and Start over have computed `#8B0000` backgrounds and
  white text;
- controls remain reachable by keyboard with visible focus, and the action
  bar does not obscure editing at any tested viewport.

Run the existing functional suite as well. These checks are implementation
acceptance criteria; the reference mockup does not replace testing of the live
page.

## Decision record

On 2026-09-23, the user selected **Balanced** from three rendered layouts, with
the `#8B0000` amendment and a full-width Speech transcript. Settings retain
their Music-mode position in Speech. The Settings rail and Text first options
were considered but not selected.
