# Deploying

One pipeline deploys everything: [`deploy/cloudbuild.yaml`](../../deploy/cloudbuild.yaml),
run by Google Cloud Build in the project itself. Nobody needs anything
installed on their own computer.

## Deploying a change

Merge the change into the production branch. The **solutioning-agent-deploy**
Cloud Build trigger starts automatically. Follow it in the console: Cloud
Build > History.

To deploy without a code change (for example after editing only secrets),
open Cloud Build > Triggers and click **Run** on solutioning-agent-deploy.

From Cloud Shell, the same pipeline runs with one command:

```bash
git clone <repository> && cd <repository>
gcloud builds submit --config deploy/cloudbuild.yaml \
  --service-account=projects/academic-diode-477405-m3/serviceAccounts/296974829876-compute@developer.gserviceaccount.com
```

## What the pipeline does

| Step | What happens | If it fails |
|---|---|---|
| Test and check config | Runs the automated tests, then checks `config.yaml` and writes every service's settings from it | Nothing has changed. Read the log: a failing test, or a config problem listed in plain words. |
| Infrastructure | Creates anything missing: BigQuery tables and new columns, the Pub/Sub queue, scheduler jobs (new ones paused), the eval bucket. Checks every secret exists. Never deletes anything, never pauses or resumes the inbox check. | Nothing has been deployed yet. A missing secret is named in the log. |
| Renderer | Deploys `solutioning-agent-renderer` | The previous renderer keeps running. |
| Services | Deploys `solutioning-agent` (private) and `solutioning-agent-onboarding` (public, same image) | The previous revision keeps serving. |
| Agent | Updates the live Agent Engine in place, keeping its id, so Gemini Enterprise and the pipeline need no change | The previous agent keeps running. |
| Evals | Runs the after-deploy evals and emails the summary | Never fails the deploy. Read the summary email. |

A full deploy takes about 20 minutes, plus 15 for smoke evals.

## Undoing a deploy

- **Cloud Run services:** Cloud Run > the service > Revisions > route 100% of
  traffic to the previous revision. Immediate.
- **The agent:** revert the change in git and deploy again.
- **A setting:** change it back in `config.yaml` and deploy.

## First-time setup (once per GitHub repository)

The pipeline's files don't name a repository, so moving to another GitHub
organisation only means repeating these steps.

1. Cloud Build > Repositories > **2nd gen**, region `us-central1`:
   **Create host connection**, provider GitHub, name `github`. Authorise
   it, and install the Cloud Build GitHub app on the organisation with
   access to this repository only. Then **Link repository** and pick it.
2. Cloud Build > Triggers, region `us-central1` > **Create trigger**:
   - Name: `solutioning-agent-deploy`
   - Event: push to a branch; repository generation 2nd gen, this
     repository, branch `^prod$`
   - Configuration: Cloud Build configuration file, `deploy/cloudbuild.yaml`
   - Service account: `296974829876-compute@developer.gserviceaccount.com`
3. Create a second trigger, also in `us-central1`:
   - Name: `solutioning-agent-weekly-evals`
   - Event: manual invocation; same repository, branch `prod`
   - Configuration: `deploy/cloudbuild-evals.yaml`
   - Same service account
4. Deploy once. The infrastructure step sees the weekly trigger and creates
   the scheduler job that runs it on `settings.evals.weekly_schedule`.

## First-time setup in a new project

Moving to a different Google Cloud project also needs, before the first
deploy: the APIs enabled (Cloud Run, Cloud Build, Vertex AI, Discovery Engine,
BigQuery, Pub/Sub, Cloud Scheduler, Secret Manager, Gmail, Drive, Slides,
Sheets, YouTube Data), the secrets created
([access-and-credentials.md](access-and-credentials.md)), a Gemini Enterprise
app with the Drive connector over the past decks folder, and the
`infrastructure` section of `config.yaml` updated. The first deploy then
creates the rest. `agent_engine_id` must name an existing engine; for a brand
new project, create one first with `adk deploy agent_engine` (without
`--agent_engine_id`) and put its id in the config.

## Branches

Work happens on `dev`, is tested on `uat`, and reaches `prod` through a
reviewed pull request. Only `prod` deploys.
