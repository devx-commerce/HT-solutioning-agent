# Research + rendering: current plan

Status: decided, not yet built (except the schema columns and doc pointers
noted in "Done" below).

Source of truth for the pilot's contracted scope: `HT Media - Gemini
Enterprise Pilot SOW (final).pdf` (Sales Pitch Agent, section 5.2, and the
acceptance metrics in section 11).

## Architecture

One agent, not a research-agent + deck-agent split: the existing
`solutioning_agent` (ADK `LlmAgent`) gets research tools added to its
existing `tools=[]` list, alongside the deck tools. One tool-calling loop
decides when to research, how much, and when to draft — no separate
top-level research agent (an independent one would make three agents against
the SOW's "two (2) pilot agents" scope).

Drafting happens via a `draft_deck(brief)` tool call that runs on a
fresh/distilled context — the findings, not the full research transcript.
Build this in from the start; research tool outputs sitting in context for
the drafting turn bloats the prompt and degrades structured-output
reliability.

## Research

**Past decks (Google Drive corpus):** native Vertex AI Search / Google Drive
connector over the past-pitch-decks folder. No custom embeddings, no
hand-rolled vector DB — out of scope per the SOW. No upfront
metadata-extraction pass either; if a field turns out to be needed later
(client name, industry) and isn't recoverable from content, add it to a
lightweight `past_deck_meta(drive_file_id, ...)` table joined onto results
after retrieval — no reindex required. Tool: `search_past_decks(query) ->
[{snippet, drive_link, title}]`.

**Web + social:** Vertex AI Grounding with Google Search as the primary tool
(covers brand/competitor/campaign/social — Google indexes public
LinkedIn/Instagram/X/Facebook posts — with real source URLs, no scraping).
`search_youtube(query)` via YouTube Data API v3 for video-specific search.
`fetch_url(url)` for the client's own site, only once a domain is trusted
(email thread first, grounded search as fallback, never guessed — skip and
note a gap rather than invent a URL).

**Deprioritized (tertiary, later):** Competitor Analysis Tool integration,
Salesforce integration.

**Grounding invariant, hard requirement:** every factual claim carries its
`source_url`. Graded by the SOW's "Evidentiary grounding" metric — 100% of
drafts must carry at least one cited prior-campaign or competitor source.

**Research brief format — loose, not rigid:**
```
notes: <free-form markdown>
findings: [{claim: str, source_url: str}]   # every claim traceable
past_precedent: [{drive_link, note}]         # only if something relevant surfaced
```
Not every request needs every section — which sections appear is driven by
what's actually found and by the email's intent, not a checklist.

**Retrieval telemetry, hard requirement:** the SOW's "Retrieval transparency"
metric requires every draft to state which sources returned results and
which didn't, including sources that returned nothing. Write one row per
source per brief to the existing `audit_log` table (already shaped for this,
already unused): `event_type='retrieval'`, `actor=<source name>`,
`outcome='success'|'no_results'|'error'`, `detail={result_count, ...}`.

## Deck rendering

No Google Slides template exists (client-confirmed) — the template-copy
approach in `build_solution_deck` is dead, not extendable.

`presentation-md` takes a structured **Deck JSON** (schema:
`packages/core/deck.schema.json`) as its canonical input — flat,
slide-indexed (`slides[i].heading`, etc.), a real fit for targeted patches.
It's Node/TypeScript; the agent is Python on Cloud Run, so it can't be
imported in-process. **Bundle Node + the presentation-md packages into the
same container image as the agent** and invoke render/export as a subprocess
(a small Node CLI script calling `@presentation-md/core`'s render +
`@presentation-md/export`'s pptx export directly — deck JSON in, pptx out).
No separate deployed service, no inter-service auth. `presentation-md` has
no partial-patch API and no Google Slides export itself — both are built on
this repo's side.

The Deck JSON **is the system of record**, stored per `brief_id` in
`briefs.deck_json`. Revision flow: load stored Deck JSON → apply a targeted
patch to the specific slide/field → re-validate with `validateDeckJson` →
re-render/export (pptx) → re-upload.

**Google Slides file handling:** preserve file ID across revisions — replace
content via Drive API `files.update`, never delete+recreate, so share
links/permissions persist. File is owned/uploaded by a service account,
locked to view-only for end users; anyone who wants to edit makes their own
copy, which naturally decouples from the automated revision pipeline.

## Delivery email

One message (SOW §5.2): the brief, an evidence summary (grounded findings
with sources), the gaps/open-questions identified, and the draft deck link —
using the SOW's own terms ("evidence summary," "gaps").

## Explicitly out of scope for now

Competitor Analysis Tool integration, Salesforce integration, AI image
generation in decks (no SOW basis; if revisited, gate on "only if no real
brand/client asset exists," fixed aspect-ratio/resolution enum enforced by
the tool call).

## BigQuery

No net-new tables. `briefs` gets two columns: `deck_json`, `research_brief`
(both JSON-as-STRING — each is 1:1 with a brief, same reasoning as any other
per-brief field). `audit_log` gets used as-is for retrieval telemetry, not
renamed — it was deliberately built generic and also carries notification
and tool-call events. `decisions`/`ingestion_threads` stay separate from
`audit_log` — they're idempotency checks the pipeline queries before acting,
not append-only history.

`lookup_deck` (finds a brief by client name across sessions, since an ADK
conversation's memory doesn't persist between them) stays, extended to also
return `deck_json`/`research_brief` — a fallback path alongside direct
brief_id lookup for when someone doesn't have the brief_id handy.

## Done

- `bigquery/schema.sql`: `deck_json`/`research_brief` columns added to the
  `CREATE TABLE` statement (does not touch the already-existing live table —
  see plan below).
- `README.md`, `docs/EMAIL-POLLER-DESIGN.md`, `archive/deck-agent-v1/README.md`:
  pointers added so the obsolete template/orchestrator-split framing isn't
  mistaken for current.

## Plan — needs console/account access

1. **Vertex AI Search (Discovery Engine) data store** over the past-pitch-decks
   Drive folder (`1P2dOh60waUoIHgeaoaGE1zYqAiBzM9F_`): enable the API, create
   the data store with the native Drive connector, create a search app,
   grant the agent's service account Discovery Engine Viewer, confirm the
   connector's identity has read access to the folder.
2. **Vertex AI Grounding with Google Search** — confirm it's enabled on the
   project and check its per-query cost (runs on every research pass).
3. **YouTube Data API v3** — enable, create a restricted key, store it in
   Secret Manager, set the env var.
4. **Apply the schema change to the live table** — `CREATE TABLE IF NOT
   EXISTS` doesn't add columns to an existing table; run `ALTER TABLE ...
   ADD COLUMN IF NOT EXISTS deck_json STRING, ADD COLUMN IF NOT EXISTS
   research_brief STRING` against the live dataset.

## Plan — code (next session)

5. Rewrite `build_solution_deck`/`update_deck` in `deck.py`: drop the
   template-copy, add Node + presentation-md to the Docker image, call it as
   a subprocess for Deck JSON → pptx, upload via Drive API preserving file
   id on revision, lock to view-only.
6. Add `search_past_decks`, `google_search`, `search_youtube`, `fetch_url`
   tools; wire into `agent.py`'s `tools=[]`.
7. Add `draft_deck(brief)` with the fresh-context behavior.
8. Extend `lookup_deck`'s `SELECT` to return `deck_json`/`research_brief`.
9. Wire `audit_log` retrieval telemetry and the evidence-summary/gaps email
   format.

Item 5 (presentation-md in the container) doesn't block 6–8 the way a
separate-service approach would have — no deploy dependency, just Docker
image work that can happen alongside the tool code.
