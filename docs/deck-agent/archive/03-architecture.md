# 03 — Architecture

## The subagent boundary

```
┌─ ORCHESTRATOR (the bigger agent) ────────────────────────────┐
│  Gmail context · BigQuery past deals · web research ·        │
│  prior decks · classification · "does this warrant a deck?"  │
│                                                              │
│         ── DeckBrief ──►  ┌───────────────────┐              │
│         ◄── DeckResult ── │  DECK SUBAGENT    │              │
│                           └───────────────────┘              │
└──────────────────────────────────────────────────────────────┘
```

**The deck subagent does no retrieval and no research.** It receives facts
and turns them into a deck. If the brief is thin, it returns
`status: "insufficient_context"` with a list of what is missing, and builds
nothing. It never invents a number, a logo, a customer name, or a claim.

That rule exists because the failure it prevents is the expensive one: a
beautiful deck full of plausible fabrications is worse than no deck, and in a
sales context it is worse than embarrassing.

### Input — `DeckBrief`

```jsonc
{
  "brief_id": "…",                  // joins to bigquery briefs table
  "client_name": "Acme Corp",
  "objective": "win Q4 branded-content renewal",
  "audience": {"role": "CMO", "familiarity": "existing client"},
  "desired_length": 10,             // advisory; planner may return ±2
  "facts": [                        // the ONLY source of truth for content
    {"id": "f1", "claim": "Renewal rate 78% across 2025 accounts",
     "value": 0.78, "unit": "pct",
     "source": "bq://pitch_agent.deals#q3-2025", "confidence": "high"}
  ],
  "assets": [                       // optional, pre-approved
    {"kind": "logo", "gcs_uri": "gs://…/acme.png"}
  ],
  "tone": "confident, concrete, no hype",
  "must_include": ["pricing tiers"],
  "must_avoid": ["competitor names"]
}
```

Every `facts[].id` is an evidence handle. Every generated claim in the IR
carries the ids it rests on, so the orchestrator (and a human) can audit any
slide back to its source.

### Output — `DeckResult`

```jsonc
{
  "status": "ok",                   // ok | insufficient_context | degraded
  "deck_id": "1AbC…",
  "link": "https://docs.google.com/presentation/d/1AbC…/edit",
  "state": "REVIEW",
  "slide_count": 10,
  "manifest": [{"n": 1, "archetype": "title", "headline": "…",
                "evidence": ["f1"]}],
  "flags": ["slide 7: scatter downgraded to table"],
  "audit": {"tier_a_clean": true, "vlm_passes": 2, "unresolved": []}
}
```

`flags` is the honesty channel. Anything the agent could not do properly goes
there and gets surfaced to the human, rather than quietly shipped.

## Pipeline

```
DeckBrief
   │
   ▼
[1] PLAN      LLM · narrative arc → beats → slides → archetype per slide
   │              → validated deck IR skeleton
   ▼
[2] CONTENT   LLM · headline (assertion, not label), body atoms, notes,
   │              chart_spec, image_brief — all within hard budgets
   ▼
[3] ASSETS    code · images generated & uploaded, cached by brief hash
   │
   ▼
[4] LAYOUT    code · DETERMINISTIC. grid solve, font-size solve via real
   │              font metrics, collision resolution. No LLM.
   ▼
[5] COMPILE   code · IR → batchUpdate array → few atomic batches
   │
   ▼
[6] AUDIT A   code · geometric checks, deterministic auto-fix, re-compile
   │
   ▼
[7] VLM LOOP  LLM · terminal only, max 2 passes, hard stop
   │
   ▼
DeckResult
```

Steps 3–6 contain no model calls. That is deliberate: it is the cheap,
testable, reproducible majority of the system.

## Deck IR

The contract between the model and the compiler. **The model emits this and
nothing else** — it never sees or writes a `batchUpdate` request.

```jsonc
{
  "ir_version": 1,
  "template_file_id": "…",
  "deck_id": null,                  // filled on first render
  "meta": {"client_name": "Acme Corp", "brief_id": "…"},
  "slides": [
    {
      "key": "s3",                  // stable across revisions — NOT the Slides object id
      "archetype": "stat_callout",
      "layout_id": "g2f3a_content",
      "headline": "Renewal held at 78% while the market fell to 61%",
      "slots": {
        "stat":    {"value": "78%", "caption": "2025 renewal rate"},
        "support": {"bullets": ["Driven by …", "Concentrated in …"]},
        "visual":  {"type": "chart", "chart_spec": { /* see 05 */ }}
      },
      "notes": "Lead with the gap, not the number.",
      "evidence": ["f1", "f4"],
      "frozen": false               // set by refinement; compiler skips frozen slides
    }
  ]
}
```

**`slides[].key` is the identity that survives revisions.** Slides object ids
are an implementation detail of the last compile and are stored separately in
the render map, never referenced by the planner or the refiner.

### Validation, before anything is written

A batch is atomic — an invalid request loses the whole deck build. So the IR
is validated hard before compiling:

- archetype exists; every required slot is filled; no unknown slots
- per-slot character and bullet budgets respected (truncate + flag, never
  silently overflow)
- `chart_spec` within caps (≤6 categories, ≤4 segments, ≤4 KPIs)
- every claim-bearing slide has at least one `evidence` id, and every id
  resolves to a fact in the brief
- no `{{PLACEHOLDER}}` tokens survive

Validation failure → one repair attempt with the errors fed back to the model
→ then fail loudly. Never compile a half-valid IR.

## ADK wiring

Maps cleanly onto primitives already in `requirements.txt`:

```python
deck_agent = SequentialAgent(
    name="deck_agent",
    sub_agents=[
        planner_agent,                       # LlmAgent → IR
        content_agent,                       # LlmAgent → IR filled
        AssetStage(),                        # code
        RenderStage(),                       # code: layout + compile + Tier A
        LoopAgent(name="polish",             # terminal VLM loop
                  max_iterations=2,          # ← the hard cap, enforced structurally
                  sub_agents=[vlm_judge, patch_applier]),
    ],
)
```

The orchestrator calls it as a **tool**, not via agent transfer:

```python
from google.adk.tools.agent_tool import AgentTool
orchestrator = LlmAgent(..., tools=[AgentTool(agent=deck_agent), ...])
```

`AgentTool` keeps control with the orchestrator and gives it a structured
`DeckResult` back. Agent transfer would hand the conversation over, which is
wrong — the orchestrator owns the user relationship and the refinement
routing.

`max_iterations=2` on `LoopAgent` is the VLM cap expressed in the framework
rather than in a prompt, which is the only place a cap actually holds.

## Storage

The IR is the state of the world, so it has to be durable.

| What | Where |
|---|---|
| Deck IR, per version | `gs://{ASSET_BUCKET}/ir/{deck_id}/{version}.json` |
| Render map (`slide.key` → Slides object ids) | alongside the IR |
| Generated images | `gs://{ASSET_BUCKET}/img/{sha256}.png` — content-addressed, so refinements reuse |
| Deck state, lifecycle, pointers | BigQuery `decks` |
| Revision journal | BigQuery `deck_revisions` |
| Audit findings | BigQuery `deck_audits` |

### BigQuery additions

Extends `bigquery/schema.sql`; `briefs` stays as it is.

```sql
CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.decks` (
  deck_id STRING NOT NULL,      -- Drive file id
  brief_id STRING,
  state STRING,                 -- DRAFTING|REVIEW|FINAL|HANDOFF
  template_file_id STRING,
  ir_uri STRING,                -- current version
  ir_version INT64,
  slide_count INT64,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.deck_revisions` (
  deck_id STRING NOT NULL,
  turn_no INT64,
  request_text STRING,
  scope_json STRING,            -- resolved {target, dimension, blast_radius}
  patch_json STRING,
  ir_uri_before STRING,
  ir_uri_after STRING,
  applied_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.deck_audits` (
  deck_id STRING NOT NULL,
  pass_no INT64,
  tier STRING,                  -- 'A' | 'VLM'
  slide_key STRING,
  check_name STRING,
  severity STRING,              -- 'error' | 'warn'
  detail STRING,
  recorded_at TIMESTAMP
);
```

`deck_revisions` is what makes "undo that" a single operation, and what makes
a bad refinement explainable after the fact.

## Tool surface

Five tools. High-level on purpose — PPTAgent measured that replacing raw
API manipulation with a clean abstraction took success from 74.6% to 95.0%.
`tools/deck.py`'s existing `lookup_deck` stays; `build_hello_deck` and
`update_deck` are superseded.

| Tool | Does |
|---|---|
| `plan_deck(brief)` | brief → validated IR. No Slides calls |
| `render_deck(ir)` | copy template → layout → compile → Tier A → link |
| `audit_deck(deck_id)` | Tier A on demand; VLM only if asked |
| `refine_deck(deck_id, request)` | scope-resolve → IR patch → minimal recompile ([08](08-lifecycle.md)) |
| `finalize_deck(deck_id, mode)` | `FINAL` (freeze) or `HANDOFF` (give the human write access) |
