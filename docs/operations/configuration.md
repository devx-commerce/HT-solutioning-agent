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
| `eval_summary_email` | sales.agent@hindustantimes.com | Receives the weekly eval summary and the after-deploy eval summary. |
| `onboarding_domains` | hindustantimes.com, htdigital.in | Google Workspace domains whose people may connect an inbox. |
| `deck_reader_domains` | hindustantimes.com, htdigital.in | Domains that can open (read only) the decks the agent creates. Applies to new decks. |
| `excluded_senders` | HR, IT, payroll and newsletter addresses | Mail from these addresses is never treated as a brief, and never sent to the model. |
| `competitor_outlets` | Times Group, Jagran, Bhaskar, Amar Ujala, Indian Express, The Hindu, The Tribune | Publications the agent never cites or names. A domain covers its subdomains. |
| `sweep_schedule` | `*/30 * * * *` | How often inboxes are checked (cron, India time). Deploying never pauses or resumes the check. |
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
| `evals.after_deploy` | smoke | Evals after each deploy: `smoke` (about 15 minutes), `full` (about an hour) or `none`. |
| `evals.smoke_cases` | pentonic, eli-lilly | Which eval cases the smoke run uses. |
| `evals.weekly_schedule` | `0 2 * * 0` | When the full weekly eval runs (Sundays 02:00 India time). |

A Drive folder or sheet id is the long code in its URL, for example
`https://drive.google.com/drive/folders/`**`1ethtG7qzfsL1QOsutGRqr9MaeN9vpzIF`**.

Before changing a model, check it is enabled in the project: Vertex AI >
Model Garden. A model that isn't enabled makes every build fail.

## Infrastructure (change only when moving projects)

The `infrastructure` section names the project, region, services, engine,
datasets, buckets and secrets. Changing it points the agent at different
resources; it doesn't move or create data. See [resources.md](resources.md)
for what each one is.

## Where settings end up

The deploy turns `config.yaml` into each service's settings:
`agents/solutioning_agent/.env` for the agent, and environment files for the
two Cloud Run services. These are generated on every deploy and never
committed; edit `config.yaml`, not them. Secrets (tokens, keys) are never in
`config.yaml`; they are read from Secret Manager.
