# Editing page layout

Status: **Ratified design contract, 2026-09-23. Delivery tracked in Issue #6.**
Amended 2026-09-29 with the rendered-clip column (RiceSuite
[#20](https://github.com/gidde032/RiceSuite/issues/20), variant C).

The [interactive reference](editor-layout-reference.html) is the approved
Balanced layout, except for the rendered column added on 2026-09-29 (see
Desktop anatomy and the Decision record), which the reference does not show
and which supersedes it where they differ. Its Music and Speech switches show the two required review
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
   **40/60** preview-to-settings split. Let the video player grow vertically to
   use the preview side's available height, with the geometry and muted-playback
   notes directly below it. Preserve the full video frame rather than cropping
   it; portrait clips can retain side letterboxing. Condense Content,
   Geometry, Header style, Burn captions, Caption style, and optional Music
   controls into short, consistently spaced rows. Keep Header and Generate in
   the settings area. The Music row ends in a **Start at** slider and a **Play
   segment** button (Issue #55). A **photo card** (Issue #54) shows a still
   preview and a **Length** row, and hides Content, Geometry, Burn captions,
   Caption style, the transcript, and the lyric pane.
   **Rendered column (Issue #20):** to the right of the settings, the upper
   row ends in the rendered clip, shown as a true **9:16** frame **540 px**
   tall (about 304 px wide) with the Download button under it. On a narrow
   or short window the frame shrinks, to `clamp(360px, min(42vw, 100vh -
   200px), 540px)`, so the settings keep room just above the breakpoint and
   The frame retains its laptop height budget. Finn accepted the Issue #21
   progress bar above the editor on 2026-10-01, including a short scroll to
   Download at some laptop sizes. Editor geometry remains unchanged. The frame sets
   the shape (the video fills it with `object-fit: cover`), so there is no
   letterbox padding, and it keeps 9:16 before the video's metadata loads.
   The 40/60 preview-to-settings split applies to the width left of it.
   The frame is always present. Before the first render, after a failed
   render, and after lyric alignment or a transcript restore replaces the
   words, it is an empty frame that marks the header band (`header_margin_v`
   to `header_margin_v` + 160 px of 1920) and the caption band (340 to 540 px
   up from the bottom) and offers no download. Any other edit after a render
   keeps that render on show, with Download, marked "Edited since this
   render" until the clip renders again. Seeking or changing volume in either
   video player is not an edit: it leaves the render current. A re-render keeps the last render in
   place, paused, dimmed and inert with a "Rendering…" note, and withholds the
   download until it finishes; Align and Restore wait for it to finish.
2. **Lower row, Music:** generated transcript on the left and optional pasted
   lyrics on the right, in **equal-width, equal-height** panes that together
   span the full card width, under the rendered column too. Both begin on
   the same horizontal line. The pasted-lyrics textarea fills its pane; Align,
   Restore transcript, and alignment status sit directly below it. Transcript
   and lyric text use identical reading styles.
3. **Lower row, Speech:** a single generated transcript pane spans the **full
   width** of the lower row. No lyric pane or replacement control panel occupies
   that row. Header, content, geometry, header style, burn-captions, caption
   style, and optional music settings remain in exactly the same upper-right
   location and order as Music mode.

**Header controls (RiceSuite #65, variant A, chosen 2026-10-06).** Under the
header-style cards, a closed **Adjust header** disclosure holds the header
controls in two columns (one column at 620 px and below): position slider,
size, font, text and edge colours, outline, soft shadow, plate, fill, opacity,
corners, padding, alignment, spacing, and Reset header. Every control meets the
36 px target (44 px with a coarse pointer). The live header preview is the
server's header PNG drawn over the source preview, in the output frame as
the render will frame the clip: the whole picture of a 9:16 source; for a
blur-pad clip, a mock of the blur-pad output fitted in the source box, drawn
from the current frame or photo; for a subject-crop clip, the centred 9:16
window with a dashed outline, as an approximation of the moving crop. The rendered-clip
column and the layout above are unchanged.

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

Generated transcript words have ordinary single-space separation, matching
pasted-lyrics text. Editable word targets add no horizontal padding between
words.

Choice cards, row padding, and ordinary action buttons become shorter than in
the current editor, while every interactive target remains at least **36 px**
high for fine pointers and **44 px** for coarse pointers. Do not reduce text
size to fit controls; wrap caption choices into more rows when needed.

## Color amendment

Use **`#8B0000`** as a solid fill with **white text** for the
`face near header` warning badge.

Each clip's remove `×` and the batch `Start over` button retain the original
Slate dark interior and text treatment, with **`#8B0000` borders**. The
warning remains text-labeled. Keep visible focus indication. The Slate palette,
other status/error colors, logo, and caption preset colors remain as specified.

## Responsive layout

At **880 px and below**, each clip becomes a single readable column in this
order: video and notes, settings, the rendered 9:16 frame (centered, at most
360 px wide), transcript, then pasted lyrics when Music is selected. Speech retains the same settings position and omits the lyric pane.
The transcript and lyric textarea use the full available column width and the
same text style. The page and controls must not overflow horizontally or hide
focus outlines. The batch actions may stack on narrow screens and must not
cover either text pane.

## Required verification for implementation

Automated browser checks must use **390×844, 768×1024, 900×800, 1024×768,
1366×768, 1440×900, and 1920×1080** viewports, in both Content modes. Pin computed geometry and
styles rather than relying on a screenshot alone:

- page inset derives from `--editor-space` at every viewport; no horizontal
  document overflow;
- desktop upper row has preview left, settings in the middle and the rendered
  column right, and narrow order is video → settings → rendered frame →
  transcript → lyrics where applicable;
- the rendered frame is 9:16 at its specified height, the video fills it with
  no padding and no letterbox (also against landscape size hints), the empty
  frame shows before a render with no download, the Rendering… note covers
  the frame, the stale note shows only on a stale render, and Download sits
  under the frame and in view at the top of the page;
- no choice card's content overflows it at any tested viewport;
- Music transcript and lyric panes have equal computed width and height and
  aligned top edges, together spanning the full lower row; Speech transcript
  spans the full lower row;
- settings remain in the same location and order across modes;
- the video player fills the preview side down to its notes on desktop and
  grows with viewport height on narrow screens without cropping;
- transcript and lyrics use identical computed Arial font, line height,
  padding, border, and background; small grey operational text also uses Arial;
- generated word gaps match a normal space in the shared reading font;
- the header warning has a `#8B0000` fill and white text; remove and Start
  over have `#8B0000` borders and the usual Slate button interior;
- controls remain reachable by keyboard with visible focus, and the action
  bar does not obscure editing at any tested viewport.

Run the existing functional suite as well. These checks are implementation
acceptance criteria; the reference mockup does not replace testing of the live
page.

## Decision record

On 2026-09-23, the user selected **Balanced** from three rendered layouts, with
the initial `#8B0000` amendment and a full-width Speech transcript. Settings
retain their Music-mode position in Speech. The Settings rail and Text first
options were considered but not selected. On 2026-09-24, the user amended the
preview height, transcript word spacing, and red-button fill from live usage;
those amendments supersede the corresponding reference details.

On 2026-09-29, Finn chose variant **C, Three-up** for the rendered clip
(RiceSuite #20) from three rendered options: fixed rail, sticky stage, and
three-up. Finn added one condition: Music mode keeps its equal
transcript/lyrics split. The decision is recorded on Issue #20.
