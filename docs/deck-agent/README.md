# Deck generator — design docs

**Live plan: [11-presentation-md-fork.md](11-presentation-md-fork.md).**
Docs 01–10 below (now in [`archive/`](archive/)) laid out building a deck
compiler from scratch on the raw Slides API — real research, but superseded
once doc 11 decided to fork and patch `presentation-md` instead, since it
already clears the design-quality bar (real editable text/tables, a native
OOXML chart object, a working theme system). That fork is developed entirely
as its own separate project at `~/Desktop/codebase/presentation-md/` — not
inside this repo. Docs 01–10 stay archived rather than deleted: the Slides
API constraints and risk analysis in them are still real and worth having if
the presentation-md path ever doesn't pan out.

The slide-deck generator is a **subagent**. Something bigger sits in front of
it: retrieval from BigQuery and past decks, web research, Gmail context,
classification. That layer decides *whether* a deck should exist and *what
it should say*. This one decides **what it looks like and how it gets built**,
and hands back an editable Google Slides link.

That boundary is the most important decision in here. The deck agent does no
research. If the brief is thin, it says so and stops — it does not invent
evidence to fill a slide.

## Read in this order

**Archived (superseded, see above) — original build-from-scratch plan:**

| | | |
|---|---|---|
| [01](archive/01-decisions.md) | **Decisions** | Build vs. integrate, and why nothing off-the-shelf works |
| [02](archive/02-constraints.md) | **Constraints** | The seven Slides API facts that dictate the architecture |
| [03](archive/03-architecture.md) | **Architecture** | Subagent contract, pipeline, deck IR, ADK wiring |
| [04](archive/04-design-system.md) | **Design system** | Template deck, archetypes, tokens, what stays creative |
| [05](archive/05-charts.md) | **Charts** | Rectangles-only renderer |
| [06](archive/06-images.md) | **Images** | Generation, style consistency, contrast guard |
| [07](archive/07-quality.md) | **Quality** | Tier A geometry, terminal VLM loop |
| [08](archive/08-lifecycle.md) | **Lifecycle** | Locking, states, conservative refinement — this philosophy (deck locked while the agent owns it, conservative refinement) still applies regardless of what renders the deck, worth keeping in mind even though the compiler itself is superseded |
| [09](archive/09-mvp.md) | **MVP** | Scope, build order, timeline, cuts — superseded by doc 11's own scope/testing plan |
| [10](archive/10-risks.md) | **Risks** | What to verify before building on it — §1 (`drive.file` may not be able to copy the template) turned out to be real, confirmed live 2026-09-23 |

## The decisions, in one page (archived — see doc 11 for what's actually being built)

**Build it, on Google's open stack.** Gemini-in-Slides already does this well
and has no API. Everything else Google ships is substrate, not capability.

1. **The LLM never writes `batchUpdate`.** It emits a validated JSON **deck
   IR**; a deterministic compiler turns that into API requests. PPTAgent
   measured this exact abstraction taking task success from 74.6% to 95.0%.
2. **Branding comes from copying a template deck**, not from API calls. There
   is no request that applies a theme — `drive.files.copy` is the only way to
   inherit masters, layouts, and theme colors. `tools/deck.py` already does
   this; it stays.
3. **Layout is arithmetic, not judgement.** Grid, type scale, spacing, and
   font-size resolution are deterministic. The model picks the archetype and
   writes the words; it never picks a coordinate or a point size.
4. **Charts are rectangles and text boxes.** No lines, no arcs, no axes. Four
   families out of one function. A Sheets-linked chart renders as an *image* —
   it fails the editability requirement, so it is not the default path.
5. **Everything editable stays editable.** Real text boxes, real shapes, real
   tables, grouped native charts. Rasters only for photography and
   backgrounds, never for text or data.
6. **QA is geometry first, vision second.** Deterministic bbox and
   font-metric checks catch most defects at zero model cost. The VLM runs
   **only at the end, capped at two passes, no exceptions.**
7. **The deck is locked while the agent owns it.** `sales.agent@` owns the
   file; the human gets `commenter`. They cannot edit mid-flight, so there is
   no reconciliation problem to solve. "Take over editing" is an explicit,
   one-way handoff.
8. **Refinement is conservative by default.** Narrowest plausible reading,
   patched at the IR level, everything unnamed frozen. One clarifying
   question only when the blast radius genuinely differs between readings.

## What this deliberately does not do

No research, no retrieval, no classification, no sending. No PPTX bridge. No
line charts in v1. No transparent-background imagery. No human co-editing
while the agent holds the deck — that is a feature, not a gap.
