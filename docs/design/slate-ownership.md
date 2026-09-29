# Slate stylesheet ownership

`ricesuite/shell/slate.css` is the sole source for the shared Slate palette,
box sizing, and top-bar surface. It ships as package data in the `ricesuite`
wheel. The shell serves it at `/shell/slate.css`; Searcher, Clipper, and Poster
serve those same bytes at `/search/static/slate.css`, `/clip/slate.css`, and
`/post/static/slate.css`. Their local routes also work when the pillar runs
alone from this checkout. Each document links the shared asset first.

`ricesuite/shell/shell.css`, `searcher/ricesearcher/web/static/styles.css`,
`clipper/web/style.css`, and Poster's embedded style own page geometry,
typography, controls, responsive rules, and states. Keep those differences
local. Searcher's softer destructive red and rice-grey focus, Clipper's
destructive red and editor spacing, and Poster's operational status hues are
intentional. The shared stylesheet exposes both the original long token names
(`--rice-grey`, `--primary-text`) and the Searcher/Clipper aliases (`--rice`,
`--text`) without changing computed colors. Its only element selector is the
shared box-sizing rule; its `.topbar` rule sets only the two common surface
properties, leaving layout to each page.

When changing a shared rule, check all four documents at desktop and narrow
widths. When changing a local component, keep it in the owning page. The
iframe documents do not inherit the shell's CSS, so a new pillar document
needs its own shared link.
