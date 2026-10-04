# 09 — MVP

## In scope

| # | Item | Notes |
|---|---|---|
| 1 | Branded template deck, **5 archetypes**, `template_manifest.json` | designer-built once ([04](04-design-system.md)) |
| 2 | `design_tokens.json` + deterministic layout solver with real font metrics | no model in the loop |
| 3 | `plan_deck` → schema-validated deck IR, 8–12 slides | one repair attempt, then fail loudly |
| 4 | `render_deck` → `files.copy` → compile → few atomic `batchUpdate` calls | |
| 5 | **Native chart renderer** — bar, column, stacked, KPI | [05](05-charts.md) |
| 6 | **Image pipeline** — deck style token, cache, scrim contrast guard, alt text | [06](06-images.md) |
| 7 | **Tier A auditor** + deterministic auto-fix | [07](07-quality.md) |
| 8 | **Terminal VLM loop** — contact sheet + per-slide, `max_iterations=2` | [07](07-quality.md) |
| 9 | **Lock + lifecycle** — commenter role, DRAFTING→REVIEW→FINAL/HANDOFF | [08](08-lifecycle.md) |
| 10 | **Conservative refinement** — scope resolver, blast-radius policy, IR patching, revision journal | [08](08-lifecycle.md) |
| 11 | `DeckBrief` / `DeckResult` contract + `AgentTool` wiring to the orchestrator | [03](03-architecture.md) |
| 12 | Returns `https://docs.google.com/presentation/d/{id}/edit` | |

## Explicitly cut

- **Live-state reconciler** — the lock makes it unnecessary. This is the
  single biggest saving and the reason the timeline did not grow.
- **Sheets-linked chart fallback** — native only; unsupported types degrade.
  Also saves adding a `spreadsheets` scope.
- **Line and area charts** — degrade to `column`.
- **Transparent / cutout imagery** — no alpha channel exists.
- **PPTX bridge** — rejected in [01](01-decisions.md).
- Multi-source ingestion, agenda/section intelligence, custom diagrams,
  tables beyond the degrade path, `logo_wall`/`timeline`/`quote` archetypes.
- Comment-triggered refinement **automation** — read comments on demand in
  v1; the polling/trigger loop is v2.

## Build order

Each stage ships something demonstrable. Do not reorder 1–4.

| | Stage | Days | Demonstrates |
|---|---|---|---|
| 1 | Template deck, manifest, tokens, layout solver | 4 | A hand-written IR renders correctly |
| 2 | IR schema + validator + compiler + `render_deck` | 5 | Deck from IR, end to end |
| 3 | **Chart renderer** | 2 | A real data slide, fully editable |
| 4 | **Tier A auditor** + auto-fix | 4 | Bad IR cannot ship a broken deck |
| 5 | `plan_deck` + `content` prompts, evidence binding | 4 | `DeckBrief` → deck |
| 6 | **Image pipeline** | 3 | Styled, cached, contrast-safe imagery |
| 7 | **Lock + lifecycle** + `finalize_deck` | 2 | Human genuinely cannot edit mid-flight |
| 8 | **Refinement** — scope resolver, IR patching, journal | 5 | "Make slide 4 punchier" touches slide 4 |
| 9 | **VLM loop** | 2 | Measurable score lift, capped at two passes |
| 10 | Orchestrator wiring, `DeckResult`, BigQuery tables | 2 | The subagent in its real place |

**~33 working days, call it 5 weeks** with integration slack.

Charts before images: higher value, deterministic, far easier to test.
Refinement before the VLM loop: refinement makes everything before it more
useful; the loop is polish on top of a working system.

## Definition of done

- A `DeckBrief` with 6 facts produces a 10-slide deck in under 90 seconds.
- Tier A clean on 100% of slides at ship time — not "mostly".
- Every claim-bearing slide traces to a `facts[].id`.
- "Change the stat on slide 4 to 81%" touches exactly one text element and
  says so.
- The requester cannot edit the deck, and File → Make a copy works.
- `flags` is empty, or every entry is something a human would agree was worth
  being told.

## Migration from the skeleton

`tools/deck.py` today: `build_hello_deck`, `lookup_deck`, `update_deck`.

- **`lookup_deck` stays** — cross-session deck recovery is still needed, and
  it is the only reason the `briefs` row exists.
- **`build_hello_deck` → `plan_deck` + `render_deck`.** The template copy and
  the BigQuery write survive; the two `replaceAllText` calls do not.
- **`update_deck` → `refine_deck`.** `replaceAllText` is exactly the
  non-conservative edit this design rejects — it is deck-wide and blind.
- `agents/pitch_agent_hello` is replaced, not patched, per its own docstring.
