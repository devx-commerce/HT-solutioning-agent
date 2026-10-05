# Cloud resources

Everything the Solutioning Agent uses, in Google Cloud project
`academic-diode-477405-m3` (number `296974829876`) unless stated otherwise.
Every resource that supports labels carries **`app=solutioning-agent`**, so
`labels.app=solutioning-agent` finds them in the console and in billing.

Other teams' work shares this project (for example the Contract Intelligence
agent, `ht-contract-*`). Nothing outside this list belongs to the Solutioning
Agent; don't change it from here.

Inventory taken 5 Oct 2026.

## Compute

| Resource | Name | Region | Notes |
|---|---|---|---|
| Cloud Run | `solutioning-agent` | us-central1 | The email pipeline (`/sweep`, `/work`). Private: only the scheduler and Pub/Sub can call it. One build at a time per instance, at most 3 instances. |
| Cloud Run | `solutioning-agent-onboarding` | us-central1 | The inbox onboarding page. Public. Same image as the pipeline, with only the onboarding routes switched on. |
| Cloud Run | `solutioning-agent-renderer` | us-central1 | Turns a deck into PowerPoint. Private: only the agent (and evals) can call it. |
| Vertex AI Agent Engine | "Solutioning Agent", `reasoningEngines/1985844993356464128` | us-central1 | The agent. Every deploy updates this engine in place; its id never changes. Label `status=live`. |
| Artifact Registry | `cloud-run-source-deploy` | us-central1 | Container images for the Cloud Run services. Shared with other teams' services, so not labelled. |

## Messaging and scheduling

| Resource | Name | Notes |
|---|---|---|
| Pub/Sub topic | `solutioning-agent-build-work` | One message per brief to build. |
| Pub/Sub topic | `solutioning-agent-build-work-dead` | Briefs that failed to build five times. |
| Pub/Sub subscription | `solutioning-agent-build-work-sub` | Pushes each brief to the pipeline's `/work`. |
| Cloud Scheduler | `solutioning-agent-sweep` | Checks connected inboxes on `settings.sweep_schedule`. Pause it in the Cloud Scheduler console to stop reading inboxes; deploys never change that. Scheduler jobs can't carry labels. |
| Cloud Scheduler | `solutioning-agent-weekly-evals` | Runs the weekly eval. Created by the deploy once its Cloud Build trigger exists. |

## Data

| Resource | Name | Notes |
|---|---|---|
| BigQuery dataset | `solutioning_agent` (US) | `briefs` (every deck, as the deck of record), `decisions` (every email the sweep judged), `ingestion_threads` (every brief from email), `audit_log` (every research call), `users` (connected inboxes), `sweep_state`. |
| BigQuery dataset | `solutioning_agent_eval` (US) | The same tables, used only by evals. |
| Cloud Storage | `academic-diode-477405-m3-solutioning-agent-evals` | Eval results and summaries. |
| Google Sheet | Briefs sheet, id in `settings.briefs_sheet_id` | One row per brief from email. |

## Secrets (Secret Manager)

| Secret | Holds |
|---|---|
| `solutioning-agent-oauth` | The agent account's (sales.agent@hindustantimes.com) access to Gmail, Drive, Slides, Sheets and search. |
| `solutioning-agent-oauth-client` | The OAuth client the onboarding page uses. |
| `solutioning-agent-state-key` | Signs onboarding links. |
| `solutioning-agent-youtube-key` | The YouTube Data API key. |
| `gmail-<16 characters>` | One per connected inbox, created automatically by the onboarding page. |

## Search and chat (Vertex AI Search / Gemini Enterprise, location `us`)

These resources don't support labels.

| Resource | Id | Notes |
|---|---|---|
| Gemini Enterprise app | `ht-sales-pitch-agent_1789729673554` ("HT-sales-pitch-agent") | Where people chat with the agent. Its agent "Solutioning Agent" points at the Agent Engine above. Web address: `https://vertexaisearch.cloud.google.com/us/home/cid/b9cac80f-5f8a-4ebf-926c-980e78d0782a` |
| Data store (Google Drive connector) | `past-decks-solutioning-agent_1790332071532_google_drive` | How the agent searches HT's past decks. Results are limited to the "Past Pitch Decks" folder. |

## Google Drive (account sales.agent@hindustantimes.com)

| Folder | Id | Notes |
|---|---|---|
| Past Pitch Decks | `1P2dOh60waUoIHgeaoaGE1zYqAiBzM9F_` | Owned by the solutioning team and shared with the agent account. The agent's library of HT's past work. If it is moved or unshared, the agent finds no past work. |
| HT brand assets | `1SMqLJ-lHAYSDMb2lvZ-kMPJIOS6TbLBN` | Holds the HT logo used on every cover. |
| Agent Generated Decks | `1ethtG7qzfsL1QOsutGRqr9MaeN9vpzIF` | Every deck the agent builds. |
| Eval Decks | `11THzL-7Z1Cw4PdlFY6Ct4VtMR74amTZd` | Inside Agent Generated Decks; decks built during evals. |

## Identities

| Identity | Used for |
|---|---|
| `sales.agent@hindustantimes.com` | The agent's own Google account. Owns the decks, sends the "deck drafted" emails, searches Drive. Needs a Gemini Enterprise licence. |
| `296974829876-compute@developer.gserviceaccount.com` | Runs the Cloud Run services, the deploy pipeline and the evals; calls the pipeline from the scheduler and Pub/Sub. |
| `service-296974829876@gcp-sa-aiplatform-re.iam.gserviceaccount.com` | The Agent Engine runtime; allowed to call the renderer. |
