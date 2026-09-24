# 11 — presentation-md fork: testing & fixing plan

Supersedes the "build vs. integrate" verdict in [01](01-decisions.md) for the
generation layer only. The design-quality bar it clears — real editable text
boxes/tables, a native OOXML chart object, a working layout/theme system for
at least 2 themes — is good enough that patching known, scoped bugs beats
building a layout solver from zero. Fork: `navyaagarwal-devx/presentation-md`,
developed entirely as its own separate project at
`~/Desktop/codebase/presentation-md/` — **not** inside this repo, not a
submodule, not vendored in here. It's a pnpm workspace, builds clean with
`pnpm install && pnpm run build`. Only once it's tested and fixed does it
enter `HT-solutioning-agent` at all, and then as a consumed package/build
artifact (exact mechanism — private npm package, git dependency, or vendored
`dist/` — TBD when we get there), never as a live source checkout sitting
inside this project's own working tree.

## Confirmed findings (all reproduced first-hand, from source, today)

| # | Finding | Where verified | Severity |
|---|---|---|---|
| 1 | `@presentation-md/shared` was never published to npm — every recent public version (1.29.0–1.34.0) of `@presentation-md/core` throws `ERR_MODULE_NOT_FOUND` on load. | npm install, 3 versions | Moot once building from the pnpm workspace — `packages/shared` resolves locally. Never depend on the published npm packages. |
| 2 | Theme *rendering* only ever searches the bundled themes dir (`packages/core/themes` — just `claude` + `default-tech`). The other 73 themes in `packages/themes/` are never wired into `loadTheme`'s search path, even building from source in the monorepo. `discoverInstalledThemes` (used only by `--list-themes`) is a separate, disconnected code path from `loadTheme` (used by actual rendering) — listing 75 themes as "available" is misleading. | `packages/core/dist/theme-loader.js` (source-read) + live render attempts | High — blocks theme selection entirely as shipped. |
| 3 | `--scaffold sales` (and likely other recipes) emits schema-**invalid** JSON by default — `timeline` slide items use `title`, the schema requires `label`. Ships broken out of the box. | live scaffold + `--audit`/render | Medium — easy to work around once known (rename at generation time), but any LLM prompted from their own docs would hit this blind. |
| 4 | `timeline` layout silently drops all `items` content — heading renders, item text never appears, in both HTML and PPTX (verified by reading raw OOXML text runs in the pptx). No audit error, no warning. Passes `--audit` clean. | reproduced twice, npm build and fork source build | High — silent data loss is the worst failure mode for an unattended pipeline. |
| 5 | Placeholder strings from `--scaffold` are literal, not templated — setting `meta.title` does not propagate; every field needs an explicit sweep or stray placeholder text ships in the final deck. | live test, found `"Untitled deck"` in final pptx text runs | Medium — process discipline, not a code fix. |
| 6 | Native `chart` layout (`bar`/`horizontal-bar`/`line`/`area`/`pie`/`donut`) produces a **real OOXML chart object** (`ppt/charts/chart*.xml`, `<p:graphicFrame>`/`c:chart`), not a rasterized image — verified by unzipping the pptx. This clears the editability bar [01](01-decisions.md) set for charts. | bar + donut tested | Positive finding — build on this, don't write a custom chart renderer. |

## Scope: what to keep, what to cut

**Keep:** `packages/core` (schema, audit, theme loader — patched), `packages/renderer-node` (HTML/PPTX render), `packages/export` (PPTX writer), the `chart` layout, `packages/themes` (all 73 manifests, selected from — none hand-authored).

**Cut:** Studio (web editor — not needed for an unattended agent), `mcp-server-legacy`.

**Open, not decided — MCP server**: don't cut by default. Revisit once we know how `tools/deck.py` actually calls into this (direct library/subprocess call vs. speaking MCP from the ADK agent). If ADK's own MCP tool-calling path ends up cleaner than a hand-rolled subprocess bridge, keep the MCP server and wire through it instead. Decide this when we get to the integration step, not now.

**Theme authoring — not doing this.** No hand-written theme.json files. Theme selection means picking from the 73 existing manifests in `packages/themes/`. If we ever need exact brand colors later, that's `import_brand_theme` (generates a theme from a brand URL/CSS automatically) — still not hand-authoring, just automated extraction — and only if the shortlist below doesn't already land close enough.

## Branching — keep upstream-PR-able

This is MIT-licensed and the bugs found are real, well-scoped, and worth
contributing back. Every fix gets its own branch off the fork's `main`, named
for the bug (`fix/theme-loader-search-dirs`, `fix/scaffold-timeline-label`,
`fix/timeline-content-loss`), each with a tight, isolated diff — not one
monolithic branch. That way any of them can become a clean PR to
`isatimur/presentation-md` on its own, independent of whatever else we've
layered on top for our own use. Our own additions that aren't upstream's
problem (the content-loss tripwire, the HT theme shortlist selection, any ADK
integration glue) live outside this branch structure entirely, in
`HT-solutioning-agent` itself, not in the fork.

## Fix list, in order

1. **Theme loader**: patch `loadTheme`'s `fallbackThemesDirs` (or the CLI wiring in `renderer-node/dist/cli.js`) to always include `packages/themes/*` alongside the bundled dir, keyed by each theme's own folder name. Unblocks every theme, not just claude/default-tech. Branch: `fix/theme-loader-search-dirs`.
2. **Scaffold generator**: fix the `sales` (and audit the other 19) recipe(s) to emit `label` for timeline items, or fix the schema/renderer to accept `title` — pick one canonical field name and make schema, scaffold, and renderer agree. Branch: `fix/scaffold-timeline-label`.
3. **Timeline layout**: find where `items` is read in the HTML template and PPTX compiler for `layout: "timeline"` and fix the drop. Given the schema/scaffold naming mismatch in #2 was invisible to `--audit`, treat this as a signal to **audit the audit** — check whether `--audit` validates that populated content is non-empty per layout, not just schema shape. Branch: `fix/timeline-content-loss`.
4. **Content-loss tripwire** (our own addition, not an upstream fix, not a fork branch — lives in our own tooling): after every render, mechanically confirm every string value in the source Deck JSON appears somewhere in the rendered HTML/PPTX text. Cheap, catches exactly the class of bug #4 without rebuilding their whole audit system.

## Testing plan — what gets generated, and for whom

Two different audiences, two different artifacts:

**For me (verification while fixing bugs):** headless Chromium/Playwright,
screenshots, HTML inspection, raw-XML text-run checks — whatever's fastest to
confirm a fix actually worked. Internal only, not something you need to look
at.

**For you (the actual review):** real `.pptx` files, nothing else. No PNGs,
no HTML players. Every deck generated during testing lands in one folder on
your Desktop (`~/Desktop/presentation-md-test-decks/`), named so you can tell
what you're opening (e.g. `theme-corporate_layout-timeline.pptx`,
`theme-broadsheet_full-sample-deck.pptx`, `chart-line_sample.pptx`). You open
them in Google Slides or PowerPoint yourself and judge fidelity directly —
that's a better test than anything I can describe.

Coverage the pptx collection needs to have:

- **Theme shortlist** (see below) × one full sample deck each, so you can compare themes on identical content.
- **Every layout type**, individually, with real HT-shaped content — 6 of 18 tested so far (title, image-hero, comparison, stat-row, feature-grid, data-table — one of these, timeline, is confirmed broken). 12 untested: two-column, quote, logo-wall, ranked-list, metric-ring, section, custom-html, and the remaining chart types below.
- **Every chart type** — bar and donut confirmed as real native OOXML objects; horizontal-bar, line, area, pie, and stacked still need the same check.
- **Image handling** — see next section, its own pptx set.

## Image handling — new testing dimension

Not yet tested: how presentation-md actually handles real photography, and
critically, whether an LLM generating the Deck JSON can be trusted to pick
sane image parameters on its own. Specifically need to find out:

- Does the tool do any resize/crop itself, or does it place the image exactly as given (meaning a wrong-aspect-ratio source image just looks wrong)?
- For `image-hero` and any other image-bearing layout (logo-wall, feature-grid icons, etc.): what dimensions/orientation does each layout actually expect?
- Given a handful of real sample images (mixed aspect ratios — portrait, landscape, square, a low-res one, a very large one), and only the same instructions an LLM would get (the skill docs / schema, no hand-holding), does a model actually choose correctly — right image for the right slot, correctly oriented, not stretched or awkwardly cropped? This is the load-bearing question: if the model can't reliably get this right from the docs alone, we either need stricter prompt constraints or a pre-flight dimension check before render, not a hope-it-works path.
- Output as its own set of pptx files in the same Desktop folder, one image scenario per file, so fidelity is checkable directly.

## Theme shortlist for review

Narrowed 75 down to newspaper/media-appropriate, professional candidates:
`corporate`, `broadsheet` (newspaper masthead aesthetic — HT is literally a
newspaper), `ft-editorial`, `heritage-editorial`, `blue-professional`,
`fintech-clean`, `swiss-typographic`. Once the theme loader is patched (fix
#1), render the same real sample deck across all seven and drop all seven
pptx files in the review folder — picked by actually opening them, not by
theme name.
