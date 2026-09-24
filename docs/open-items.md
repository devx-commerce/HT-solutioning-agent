# Deprioritized and open items

Running catch-all so deferred/unresolved things don't get lost. Not a status
tracker for in-progress work — see `docs/research-and-rendering-decisions.md`
for the active plan. Add to this file whenever something comes up that isn't
being acted on right now.

## Deprioritized (explicitly deferred, not being built now)

- **Competitor Analysis Tool integration** (named in the SOW as a source for
  competitor print activity/category share of voice). Tertiary — revisit
  only if the client asks. Vendor not yet identified.
- **Salesforce integration** (account/opportunity/conversation history, also
  named in the SOW). Tertiary, same as above.
- **AI image generation in decks.** No SOW basis for this at all. If picked
  up later: gate on "only if no real brand/client asset exists," fixed
  aspect-ratio/resolution enum enforced by the tool call (not model-chosen
  dimensions), no text baked into the image.
- **Client/HT brand asset lookup** (logo fetch by domain, HT's own fixed
  brand assets). Designed conceptually — domain-based logo API with a
  favicon fallback, "don't guess" if nothing resolves — but not built. Not
  required for the first working demo.
- **User-uploaded image → attach to a specific slide** (someone hands the
  agent an icon/logo mid-chat and asks it to place it). Fits the existing
  patch-and-reupload revision pipeline conceptually, but depends on an
  unconfirmed platform capability — see the open question below.

## Open questions (need an answer, not yet answered)

- **How should the past-decks Drive data store be scoped, and which identity
  does the agent search as?** Blocks `search_past_decks`. What's established:
  a Drive data store is `{contentConfig: "GOOGLE_WORKSPACE", aclEnabled: true,
  workspaceConfig: {type: "GOOGLE_DRIVE"}}`, and two already exist in this
  project (HT's own — not ours, don't touch), so Workspace consent has been
  granted here before. Unresolved:
  1. Can a Drive connector be scoped to one folder ("Past Solution Decks"),
     or does it index the whole domain/Drive? If it's whole-Drive, searches
     need a filter so results are past decks and not arbitrary HT documents —
     and indexing HT's entire corporate Drive is a decision for HT, not us.
  2. With `aclEnabled`, search is ACL-filtered per end user. For the email
     pipeline there is no interactive user — presumably it should search as
     the account manager whose mailbox triggered the brief. Needs deciding.
  Not attempted autonomously: creating this touches how much of HT's Drive
  gets indexed, which is theirs to approve.

- **Does Gemini Enterprise's chat surface forward an uploaded file's bytes
  (or a storage reference) to the agent in a form a tool function can
  access?** Not confirmed by anything in this repo or the SOW — no
  file-upload handling exists in the code. Test directly in the GE chat
  (attach an image, ask the agent to describe what was sent) before building
  the attach-to-slide tool around an assumption.
- **How does the vendor/tool for the Competitor Analysis Tool integration
  expose its data** — API, scheduled export, or UI-only? SOW (§10.3.viii)
  says this needs confirming with the client; not yet done. Blocks that
  integration whenever it's picked back up.

## Housekeeping

- **Label every resource that's ours with `app=solutioning-agent`.** This runs
  in HT Media's own GCP project (`academic-diode-477405-m3`) alongside
  unrelated internal HT work. Inventory taken 2026-09-25:

  **Ours** (safe to label):
  - Cloud Run: `solutioning-agent`, `solutioning-agent-onboarding`
  - Secrets: `solutioning-agent-oauth`, `solutioning-agent-oauth-client`,
    and the per-mailbox `gmail-<sha256[:16]>` secrets our own onboarding
    creates (`app/auth/gmail_oauth.py:139`)
  - Pub/Sub: `solutioning-agent-build-work`, `-dead`, `-sub`
  - Cloud Scheduler: `solutioning-agent-sweep`
  - BigQuery dataset: `solutioning_agent`
  - Agent Engine `reasoningEngines`: the four named "Solutioning Agent"
    (2026-09-21/22)

  **HT's — do not touch, do not label, do not query:**
  - Cloud Run: `gfpgan-service`, `ht-media-mediclaim-enrollment`,
    `orgchart-pro`
  - Secret: `password`
  - BigQuery dataset: `contracts` (may be intended for the Contract
    Intelligence Agent, but we did not create it — treat as theirs)
  - All five existing Discovery Engine data stores (`drive_*`,
    `drive-done_*`, `sfdc-new-test_*_opportunity`, two `*-gcs-connector_*`)
  - Agent Engine `reasoningEngines` named `Agent_Editor*` (Feb 2026)

  **Caveat before labelling Cloud Run:** setting labels on a Cloud Run
  service creates a new revision, i.e. a redeploy. Don't do that
  immediately before a demo — label the zero-risk resources (secrets,
  dataset, Pub/Sub, scheduler) first and do Cloud Run in a quiet window.

- **Four duplicate "Solutioning Agent" Agent Engine resources exist** from
  repeated `adk deploy agent_engine` runs, which create a new resource each
  time rather than updating in place. Only one is wired to GE. Worth pruning
  the stale ones eventually — carefully, since deleting the live one breaks
  the GE registration.

## Ideas not yet designed

- **Sender blacklist for ingestion.** Certain senders (HR, IT service desk,
  internal newsletters) should never reach classification — filtered at
  intake, before the Gemini call. Worth knowing before building it:
  classification already *rejects* these correctly (checked against the real
  inbox on 2026-09-25 — HR's "IJP | Digital Sales Strategy", the IT/SFDC
  thread and a marketing newsletter all classified as not-a-request), so
  this is a cost and noise optimisation, not a correctness fix. The one
  genuine correctness case — the agent reading its own notifications back —
  is already handled in `ingestion._self_filter`. Still open: where the list
  lives (BigQuery vs config), address-exact vs domain-level, who maintains
  it.

## Log

- 2026-09-25: file created, seeded from items deprioritized during the
  research/rendering planning conversation.
