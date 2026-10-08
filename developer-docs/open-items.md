# Deprioritized and open items

Running catch-all so deferred/unresolved things don't get lost. Not a status
tracker for in-progress work — see `developer-docs/research-and-rendering-decisions.md`
for the active plan. Add to this file whenever something comes up that isn't
being acted on right now.

## Deprioritized (explicitly deferred, not being built now)

- **Competitor Analysis Tool integration** (named in the SOW as a source for
  competitor print activity/category share of voice). Tertiary — revisit
  only if the client asks. Vendor not yet identified.
- **Salesforce integration** (account/opportunity/conversation history, also
  named in the SOW). Tertiary, same as above.
- ~~AI image generation in decks~~ and ~~client/HT logo lookup~~: built
  2026-10-01 in `tools/visuals.py` (HT confirmed generated images are fine
  for generic deck use). See "Unverified until credentials are restored"
  below.
- ~~User-uploaded image → attach to a specific slide~~: built 2026-10-01 as
  `place_image_from_chat` (checks format, size, resolution and shape against
  the slot, and refuses with a reason the agent passes on). Works when the
  image reaches the agent; whether Gemini Enterprise delivers it is still
  the open question below.

## Needs HT to confirm

- **Competitor publications excluded from research** (`tools/source_policy.py`,
  confirmed fine by the project lead 2026-10-01). Web findings whose only sources
  are on these domains are dropped, and the agent is told not to name them:
  - Times Group: indiatimes.com (TOI, ET, ET BrandEquity, Navbharat Times),
    timesofindia.com, economictimes.com, maharashtratimes.com
  - Dainik Jagran (jagran.com), Dainik Bhaskar (bhaskar.com,
    divyabhaskar.co.in), Amar Ujala (amarujala.com)
  - The Indian Express (indianexpress.com), The Hindu (thehindu.com), The
    Tribune (tribuneindia.com)

  Deliberately **not** on it, pending HT's view: sister titles of the above
  (Financial Express, Jansatta, The Hindu BusinessLine, Mid-day, Naidunia),
  Business Standard, NDTV, and TV news sites. Why it matters: the Rapido
  deck of 2026-09-28 cited ET/BrandEquity five times and TOI once, the TOI
  link backing a stat-row figure.

## Unverified until credentials are restored

Built and unit-tested on 2026-10-01 while local application-default
credentials had expired, so these have not run against the live project:
- ~~Image generation~~: verified live 2026-10-01 on `gemini-2.5-flash-image`
  (global). No Imagen model, 3.0 or 4.0, is reachable in this project.
- ~~HT logo from the asset folder~~: verified live 2026-10-01.
- **Client logo lookup** was run live from a laptop against ten past
  clients' sites: Rapido, Fortis and Nissan resolved correctly; Tata
  Sampann (timeout), Nestlé (403 to bots), Muthoot (WebP only), Lulu, AMD,
  Signify, Harvest Gold and Agilus (SVG-only or no marked logo) fall back
  to the placeholder. SVG/WebP support would raise the hit rate; it needs a
  rasteriser, which nothing in the agent package has.

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

  **Status 2026-09-25 (post-admin-change): still returning zero, and the
  fault is provably not ours.** Evidence, so this isn't re-diagnosed:
  1. Drive API as `sales.agent@` lists all 19 decks in the folder, and
     Drive's own `fullText contains 'Maggi'` finds the deck. Drive
     permissions, the identity and the scopes are fine.
  2. Engine `ht-sales-pitch-agent_1789729673554` is wired to
     `solutioning-agent-past-decks_1790319778373_google_drive`
     (`GOOGLE_WORKSPACE`, `aclEnabled`, `dasherCustomerId C03jr3210`).
     Connector `ACTIVE`, `FEDERATED`, `errors: []`.
  3. Searching the engine *and* the data store directly returns 0 results
     with HTTP 200. Cloud Logging records these as `INFO` with no status
     message — the backend genuinely returns nothing; it is not a
     suppressed permission error.
  4. A **second Drive connector** (`drive-connector_1790312264757`) fails
     identically — and audit logs show it was created by
     `manish.aggarwal@hindustantimes.com`, HT's own Workspace admin, at
     04:57 UTC. So the failure is not specific to a connector built by an
     external `@devxlabs.ai` identity. (This also explains the previously
     "unexplained Gmail connector" — same admin, same morning. That one is
     worse off: `INITIALIZATION_FAILED`, *"pipeline failure. Please delete
     connector and retry creation."*)
  5. Gemini Enterprise's own first-party chat also retrieves nothing — and
     hallucinates a plausible proposal instead of saying so.

  **Ruled out:** smart features. Both per-user toggles were confirmed on
  for `sales.agent@` on 2026-09-25 ("Smart features in Google Workspace"
  was already on and is the one covering Drive; "in other Google products"
  was switched on too). Searches still returned zero immediately after.
  Note Workspace setting changes can take up to 24h to propagate, so a
  delayed recovery would not be surprising.

  **RESOLVED 2026-09-25 — it was the API surface, not the configuration.**
  A `FEDERATED` Workspace data store returns nothing from
  `SearchService.search` (`servingConfigs/default_search:search`), which is
  what `search_past_decks` was calling. The same serving config answers
  correctly on **`:answer`** (and on
  `assistants/default_assistant:streamAssist`). Verified: query
  "Maggi proposal" returned real deck content — the Nukkad Natak activation,
  "Sirf paanch rupaye mein", 400 Nukkad Nataks across 50 districts in UP,
  Nov–Dec '21 — with 34 citations and references carrying
  `title` ("Maggi Masala ae Magic X HT Media.pptx"), a Drive `uri`, and the
  Drive file id (`1i07uWMxf-gE4ZLuSsxaWYalPKDOwfzob`, parent
  `1W7C53D5nLxFQTX0GuAeaIYWzjuWVr7MO` — so the existing folder allow-list
  still applies unchanged).

  This is why every config check passed: nothing *was* misconfigured. The
  connector never indexes (federated = live query), so there are no
  documents for `:search` to match; retrieval only happens inside answer
  generation. Zero results with HTTP 200 and an `INFO` log is the expected
  shape of that, not a fault.

  **Done** — `search_past_decks` now calls
  `ConversationalSearchServiceClient.answer_query` (v1alpha) and returns
  `results` (deduped decks) plus `findings` (`{claim, sources}`). Verified
  live against 10 brands. Three things learned while porting, each now
  covered by a test:
  - **v1alpha, not v1.** v1's `answer_query` returns "a summary could not be
    generated" against this same serving config.
  - **Citation offsets are UTF-8 byte positions**, not character positions —
    slicing the `str` mangles any claim containing `₹` or an em dash.
  - **Bare one-word queries are rejected** with
    `OUT_OF_DOMAIN_QUERY_IGNORED` ("Rocksport" finds nothing, "Rocksport
    proposal" finds the deck). The agent instruction now says to use a
    phrase and retry before concluding there is no prior work.

- **The past decks are in one Drive folder: the solutioning team's
  original.** `1P2dOh60waUoIHgeaoaGE1zYqAiBzM9F_` ("Past Pitch Decks",
  owned by ankita.suden@htdigital.in, shared with sales.agent@). Until
  2026-09-28 there was also a working copy, `1W7C53D5nLxFQTX0GuAeaIYWzjuWVr7MO`
  ("Past Solution Decks", owned by sales.agent@), made before the original
  was shared. A file-by-file check on 2026-09-28 found the copy held nothing
  the original lacks (identical checksums; the original also has the Lulu
  Mall deck), so the copy was retired and `PAST_DECKS_FOLDER_ID` lists only
  the original. While both existed, both had to be allow-listed: the
  connector returns whichever copy Drive's index prefers, and with one
  folder listed about half the corpus was silently dropped ("Muthoot
  Finance" retrieved 10 correct references and every one was filtered out,
  logging `no_results`, which reads as "HT never pitched them").
  **Two things still aren't detected by anything:** the solutioning team
  moving decks to a new folder, and them unsharing this one from
  sales.agent@. Either makes the corpus look empty rather than failing
  loudly.

- **Does Gemini Enterprise's chat surface forward an uploaded file's bytes
  (or a storage reference) to the agent in a form a tool function can
  access?** Still unconfirmed. `place_image_from_chat` now accepts every
  shape ADK can deliver one in (inline bytes on the message, a gs:// or
  https file reference, or a saved artifact, in this message or an earlier
  one) and is verified locally with inline bytes. If GE forwards none of
  these, the tool replies "No image is attached". Test after the next
  deploy: attach a 1920×1080 JPEG in a GE chat and ask for it on a deck's
  big idea slide.
- ~~Who can open the decks?~~ Both `hindustantimes.com` and `htdigital.in`
  are HT's own; `DECK_READER_DOMAIN` is now comma-separated and set to both
  (2026-10-01). The deployed config must be updated to match.
- **How does the vendor/tool for the Competitor Analysis Tool integration
  expose its data** — API, scheduled export, or UI-only? SOW (§10.3.viii)
  says this needs confirming with the client; not yet done. Blocks that
  integration whenever it's picked back up.

## The past-decks search depends on a Gemini Enterprise seat

`sales.agent@hindustantimes.com` must hold a Gemini Enterprise licence or
every past-decks search fails with *"User must be assigned a license"*. This
is not IAM and not a scope — it is a paid seat, and it is easy to lose:

- The active subscription (`f61fcac8-67f3-47e6-8d9b-3ee74512aecc`, Gemini
  Enterprise Plus) has **4 seats, all occupied**. The 50-seat
  `free_trial_gemini` expired 6 Jan 2026.
- The seat `sales.agent@` holds was freed by unassigning
  `navya.agarwal@devxlabs.ai` on 2026-09-25. Nothing marks it as
  infrastructure, so a routine cleanup could reclaim it and the agent would
  start reporting "no prior HT work" with no obvious cause.
- **The subscription renews 21 Oct 2026, mid-pilot** (SOW term ends 31 Oct).
  Worth confirming seats survive the renewal.
- SOW §5 describes up to 50 pilot users; 4 seats cannot cover that, so seat
  count needs raising before any rollout beyond the demo.

Reassigning is self-serve — `roles/discoveryengine.admin` includes
`discoveryengine.userStores.batchUpdateUserLicenses` — via Gemini Enterprise
→ Manage users. Administration of data stores and apps does **not** need a
licence (verified: an unlicensed identity can list data stores and get
engines); only end-user `servingConfigs.search` does.

## Credential facts worth not rediscovering

Three scope lists must agree, and `scripts/get_refresh_token.py` holds the
one that matters: it decides what the token is *granted* at consent, while
the two `oauth_creds.py` copies only decide what is *requested* on refresh.
Add a scope to the script and re-mint first; adding it to `oauth_creds.py`
before the token carries it fails every refresh with `invalid_scope`, which
takes down Drive, Slides, Sheets and Gmail send together.

Searching the Workspace store only works as a `hindustantimes.com` identity
— a service account or any `@devxlabs.ai` account is refused with "User does
not belong to the same organization" — and needs `cloud-platform`, which
Discovery Engine offers no narrower alternative to.

Secret Manager's `versions/latest` resolves to the highest-numbered version
**even when that version is disabled**, so a bad version cannot be fixed by
disabling it. Supersede it with a new version instead.

## Committed in the SOW but not yet done

- **Agree a data-loss-prevention policy with HT.** SOW §5.2 commits to
  "Agree the security configuration applied to each agent, including data
  loss prevention masking," and the Vertex AI Search data store has a
  "sensitive data protection policy" field expecting
  `projects/{project}/locations/{location}/contentPolicies/{policy}`. No
  policy has been agreed, so the field is currently blank. Needs a decision
  with HT about what to mask, then the policy created and attached.

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

- **Duplicate Agent Engine resources: resolved (checked 8 Oct 2026).** Only
  the live Solutioning Agent engine (`1985844993356464128`) remains; the other
  engines in the project belong to other HT teams.

## Ideas not yet designed

- ~~Sender blacklist for ingestion~~: built 2026-10-01 as
  `ingestion.EXCLUDED_SENDERS` (HR, payroll, IT help desk and IT communications, the "Happening Now" newsletter, Darwinbox), excluded in
  the Branch A Gmail query; Branch B (manual label) is never filtered. Notes
  from before it was built:
  **Sender blacklist for ingestion.** Certain senders (HR, IT service desk,
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
- 2026-10-01: competitor source list, unverified-live items; image
  generation, logos and the sender exclusion list moved to built. Decks now
  go to the "Agent Generated Decks" folder (`DECK_FOLDER_ID`
  1ethtG7qzfsL1QOsutGRqr9MaeN9vpzIF, owned by sales.agent@).
