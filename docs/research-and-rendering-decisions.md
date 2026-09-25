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

**A GCS mirror of the decks is not an acceptable substitute.** A Drive
data store is `contentConfig: GOOGLE_WORKSPACE` with `aclEnabled: true`, so
results are filtered per user by real Drive permissions; SOW §5.3 requires
exactly that ("verify that results returned from Drive respect each user's
existing file permissions"). Copying decks into a bucket and indexing that
would bypass per-user ACLs and break the requirement, whatever else it
made easier.

Corpus location: the Drive folder **"Past Solution Decks"**
(`1W7C53D5nLxFQTX0GuAeaIYWzjuWVr7MO`), 20 decks, readable by
`sales.agent@`. The folder id in earlier notes (`1P2dOh60...`) is not
reachable by that identity — this one supersedes it.

**Web + social:** Vertex AI Grounding with Google Search as the primary tool
(covers brand/competitor/campaign/social — Google indexes public
LinkedIn/Instagram/X/Facebook posts — with real source URLs, no scraping).
`search_youtube(query)` via YouTube Data API v3 for video-specific search.
`fetch_url(url)` for the client's own site, only once a domain is trusted
(email thread first, grounded search as fallback, never guessed — skip and
note a gap rather than invent a URL).

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
It's Node/TypeScript, so it can't be imported into Python in-process.
`presentation-md` already ships a CLI that does exactly what's needed —
`node packages/renderer-node/dist/cli.js -f pptx -o out.pptx` reading Deck
JSON on stdin, validating it against deck.schema.json first — so no bridge
script is needed, just a subprocess call. That's what `PRESENTATION_MD_CLI`
points at, and it's how `tools/deck.py` renders today.

**Unresolved for deployment: nothing in the deployed topology can run Node
yet.** The agent deploys to Vertex AI **Agent Engine** (`adk deploy
agent_engine`), a managed Python runtime with no Node and no container of
ours. The Cloud Run service builds from **buildpacks** (`--source=.` plus a
Procfile), also Python-only, with no Dockerfile to add Node to. So the
subprocess path works locally and in tests but not in the deployed agent.
Two ways out, to pick between:
  1. Give the Cloud Run service a Dockerfile with Python + Node +
     presentation-md, expose an internal render endpoint, and have the
     agent tool call it. One service, but converts a working buildpack
     deploy to a Dockerfile.
  2. A small separate Node-only Cloud Run service that does Deck JSON →
     pptx. Leaves the working service untouched; adds a second deployment.
Either way the agent tool needs an HTTP backend alongside the subprocess
one. `presentation-md` has no partial-patch API and no Google Slides export
of its own — both are built on this repo's side.

**Don't install the renderer from public npm.** `@presentation-md/render` is
published (1.20.9, same version string as the local checkout), which makes
`npm i -g` tempting, but the local `test/combined-fixes-local` branch
carries ~10 pptx-export fixes that are not in `origin/main` and so not in
the published package — ranked-list items invisible on light themes, a
`cardRadius()` falsy-zero bug, pptxgenjs defaulting `line width: 0` to a
visible 1pt, theme chrome washes. Installing from npm would silently ship
decks with those defects back. The deployed renderer has to come from the
fork: published to a private registry, or its built `dist` vendored into
whatever image runs it.

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

Deprioritized/deferred items (Competitor Analysis Tool, Salesforce, image
generation, etc.) live in `docs/open-items.md`, not here.

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

## Built and verified (2026-09-25 overnight)

Working, on branch `feat/research-tools`:

- **Research tools** (`agents/solutioning_agent/tools/research.py`):
  `search_web` (Vertex AI grounding with Google Search, claims paired with
  resolved source urls), `fetch_url`, `search_youtube` (needs a key),
  `search_past_decks` (needs the data store). All four log per-source
  retrieval telemetry to `audit_log`, including when they return nothing.
- **Deck tools** (`agents/solutioning_agent/tools/deck.py`): Deck JSON →
  pptx via the presentation-md CLI → Drive upload as native Slides →
  read-only lock; targeted per-slide edits re-render and replace the file
  **keeping its id**. No Slides `batchUpdate` anywhere.
- **Agent** wired with all seven tools and an instruction covering research
  discipline, citation, gaps, the Deck JSON layouts, and no pricing.
- **Delivery email** carries the SOW's four parts, with "sources checked"
  read back from telemetry rather than from the model's self-report.
- Verified end-to-end against the real Lulu Mall brief: the agent searched
  past decks first, reported the corpus as unreachable rather than implying
  no prior work existed, ran iterative grounded web searches, and built a
  real deck. Deck JSON round-trip (build → patch → re-render → same file
  id) verified against live Drive and BigQuery.

Known rough edge: the model sometimes needs a retry or two to emit valid
Deck JSON. Raw control characters in strings are now normalized away
automatically; schema mistakes still cost a round-trip, but the schema
error is fed back and it self-corrects.

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
3. ~~YouTube Data API v3~~ — **done.** Key restricted to
   `youtube.googleapis.com` only, stored as Secret Manager secret
   `solutioning-agent-youtube-key` (labelled `app=solutioning-agent`).
   Cloud Run reads it via
   `--set-secrets=YOUTUBE_API_KEY=solutioning-agent-youtube-key:latest`; the
   Agent Engine package reads it from its bundled `.env`. Verified live
   against Air India.
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
