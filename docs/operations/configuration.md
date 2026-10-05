# Configuration

Every setting lives in [`config.yaml`](../../config.yaml) at the root of the
repository. Each has a working default and a comment saying what it does.

To change a setting: edit `config.yaml`, commit, and deploy
([deploy.md](deploy.md)). The deploy checks the whole file first. If anything
is wrong, it stops before changing anything and lists every problem in plain
words, for example:

```
config.yaml has problems; nothing was deployed:
  - max_slides: must be a whole number from 7 to 30 (found 50).
  - onboarding_domains: must be a list of domains like hindustantimes.com, without @ (found ['@htdigital.in']).
```

## Settings (safe to change)

| Setting | Default | What it does |
|---|---|---|
| `eval_summary_email` | sales.agent@hindustantimes.com | Receives the weekly usage email and the eval summary. |
| `onboarding_domains` | hindustantimes.com, htdigital.in | Google Workspace domains whose people may connect an inbox. |
| `deck_reader_domains` | hindustantimes.com, htdigital.in | Domains that can open (read only) the decks the agent creates. Applies to new decks. |
| `excluded_senders` | HR, IT, payroll and newsletter addresses | Mail from these addresses is never treated as a brief, and never sent to the model. |
| `competitor_outlets` | Times Group, Jagran, Bhaskar, Amar Ujala, Indian Express, The Hindu, The Tribune | Publications the agent never cites or names. A domain covers its subdomains. |
| `usage_report_schedule` | `0 9 * * 1` | When the weekly usage email goes out (cron, India time; Mondays 09:00). |
| `sweep_schedule` | `*/5 * * * *` | How often inboxes are checked (cron, India time). Deploying never pauses or resumes the check. |
| `new_inbox_lookback_hours` | 24 | How far back a newly connected inbox is read on its first check (0 to 168). 0 reads only mail arriving from then on. |
| `max_slides` | 20 | Longest deck the agent may build (7 to 30). |
| `min_images` | 3 | Pictures a new deck must have (0 to 10). Existing decks stay editable whatever this is. |
| `models.agent` | gemini-3.8-flash | Plans research, drafts and revises decks. |
| `models.research` | gemini-3.8-flash | Web research with Google Search. |
| `models.classify` | gemini-2.5-flash-lite | Decides whether an email is a brief. |
| `models.images` | gemini-2.5-flash-image | Generates slide pictures. |
| `deck_folder_id` | "Agent Generated Decks" | Drive folder new decks are created in. |
| `past_decks_folder_ids` | "Past Pitch Decks" | Folders the agent may cite as HT's past work. |
| `ht_assets_folder_id` | HT brand assets | Folder holding the HT logo (any image with "logo" in its name). |
| `briefs_sheet_id` | The briefs sheet | Sheet that gets one row per brief from email. |
| `evals.after_deploy` | none | Evals after each deploy: `none`, `smoke` (about 15 minutes) or `full` (about an hour). Otherwise evals run only when started by hand. |
| `evals.smoke_cases` | pentonic, eli-lilly | Which eval cases the smoke run uses. |

A Drive folder or sheet id is the long code in its URL, for example
`https://drive.google.com/drive/folders/`**`1ethtG7qzfsL1QOsutGRqr9MaeN9vpzIF`**.

Before changing a model, check it is enabled in the project: Vertex AI >
Model Garden. A model that isn't enabled makes every build fail.

## Infrastructure (change only when moving projects)

The `infrastructure` section names the project, region, services, engine,
datasets, buckets and secrets. Changing it points the agent at different
resources; it doesn't move or create data. See [resources.md](resources.md)
for what each one is.

`gemini_enterprise_agent_url` is the Solutioning Agent's own address in
Gemini Enterprise, used for the deep link to the refinement loop in each
"deck drafted" email. To find it, open the agent from Agents in Gemini
Enterprise and copy the address up to and including `/r/agent/<id>`, leaving
out any `/u/1/` (which names one person's browser account).

## Where settings end up

The deploy turns `config.yaml` into each service's settings:
`agents/solutioning_agent/.env` for the agent, and environment files for the
two Cloud Run services. These are generated on every deploy and never
committed; edit `config.yaml`, not them. Secrets (tokens, keys) are never in
`config.yaml`; they are read from Secret Manager.
