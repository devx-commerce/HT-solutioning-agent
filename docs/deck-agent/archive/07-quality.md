# 07 — Quality

Two tiers, in this order, because AeSlides measured that **rule-based
verifiable metrics beat VLM detection on both accuracy and cost**, and that
post-hoc visual reflection is expensive for marginal gain. Geometry first.
Vision last, briefly, and capped.

## Tier A — deterministic

Runs on every slide, every compile. No model calls. `presentations.get`
returns `size` and `transform` for every element, so exact bounding boxes are
computable without rendering anything.

### Slide checks

| Check | Rule |
|---|---|
| **Overflow** | predicted text height (real font metrics, real wrapping, resolved size) > box height. The #1 defect; the API will not catch it ([02](02-constraints.md) §2) |
| **Collision** | pairwise bbox intersection above threshold |
| **Out of bounds** | any element crossing the margin ring or the canvas |
| **Alignment** | element edges snap to grid columns within ±2 pt |
| **Whitespace** | ink-coverage ratio outside `[0.18, 0.62]` |
| **Imbalance** | visual centroid offset from canvas centre above threshold |
| **Contrast** | WCAG AA for every text-on-fill and text-on-image pair |
| **Density** | words/slide, bullets/slot, bullet length vs. `budgets` |
| **Placeholders** | no surviving `{{TOKEN}}` |

Chart internals are covered by the same checks, because charts are rectangles
([05](05-charts.md)).

### Deck checks

Archetype run-length (no 3 in a row) · headline n-gram repetition · type
sizes all drawn from the ladder · one accent colour · image style token
consistent.

### Auto-fix

Tier A failures are fixed deterministically, then re-compiled and re-checked:
step down the type ladder · truncate at a word boundary and flag · insert a
scrim · re-solve the slot · split the slide · drop to the next archetype.

A slide that fails Tier A twice falls back to `statement` with the headline
only, and is flagged. It never ships broken.

## Tier B — the VLM loop

**Terminal. Two passes. Hard stop.** Enforced by `LoopAgent(max_iterations=2)`
([03](03-architecture.md)), not by a prompt, because a prompt is not a cap.

It runs only after Tier A is clean. Never mid-build, never per slide as
slides are made.

```
Tier A clean
   │
   ▼
PASS 1
  a) CONTACT SHEET — tile all N thumbnails into ONE image, one model call.
     Deck-level judgement only: repetition, visual rhythm, palette drift,
     coherence, pacing. Cheap, and it sees what slide-by-slide cannot.
  b) PER-SLIDE — only slides flagged by (a), plus Tier A near-misses.
     PPTEval rubric, 1–5 on content / design / coherence, plus a REQUIRED
     specific actionable defect string. A score with no defect string is
     discarded.
   │
   │  patch only slides scoring < 3.5, at the IR level
   │  re-run Tier A on touched slides (cheap, no render)
   ▼
PASS 2
  re-render ONLY the slides pass 1 changed. Judge again.
  still failing → fall back to the safest archetype, or ship and FLAG.
   │
   ▼
STOP. There is no pass 3.
```

### Why the contact sheet first

Deck-level defects — three near-identical layouts in a row, palette drift,
monotonous rhythm — are invisible slide-by-slide and cheap to catch in one
tiled image. It also cuts render calls, which matters because `getThumbnail`
is an expensive read and its URLs expire in ~30 minutes
([02](02-constraints.md) §5).

### Budget guard

If pass 1 flags more than 40% of slides, that is a **planning** failure, not
a polish failure. Abort the loop, return `status: "degraded"` with the
finding, and let the orchestrator decide. Do not burn two passes papering
over a bad plan.

### On refinement turns

The VLM loop does **not** run by default. Only on slides actually touched,
and only if the user asked for a visual check or Tier A flagged something.
See [08](08-lifecycle.md).

## What we measure

Per deck, into `deck_audits`: Tier A findings by check, VLM scores per pass,
slides changed per pass, thumbnails rendered, total model cost. If Tier A
findings do not fall as the archetype library matures, the design system is
wrong, not the model.
