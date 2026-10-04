#!/usr/bin/env bash
# Create whatever the agent needs that doesn't exist yet. Safe to run on
# every deploy: it never deletes anything, never pauses or resumes the inbox
# sweep, and leaves anything already in place as it is (except the sweep's
# schedule, which follows config.yaml).
#
# Reads deploy/out/deploy.env, written by `python -m deploy.render_config`.
set -euo pipefail
cd "$(dirname "$0")/.."
source deploy/out/deploy.env

P="--project=$PROJECT_ID"
LABEL="app=solutioning-agent"
RUNTIME_SA="$PROJECT_NUMBER-compute@developer.gserviceaccount.com"
say() { echo "[infra] $*"; }

# --- secrets: these hold credentials, so they can only be checked, not made ---
missing=()
for name in $SECRET_NAMES; do
  gcloud secrets describe "$name" $P >/dev/null 2>&1 || missing+=("$name")
done
if (( ${#missing[@]} )); then
  echo "These secrets are missing from Secret Manager: ${missing[*]}."
  echo "See docs/operations/access-and-credentials.md to create them, then deploy again."
  exit 1
fi

# --- BigQuery: datasets, tables, and columns added since a table was made ---
for ds in "$BQ_DATASET" "$EVAL_DATASET"; do
  if ! bq --project_id="$PROJECT_ID" show "$ds" >/dev/null 2>&1; then
    bq --project_id="$PROJECT_ID" --location=US mk --dataset --label="app:solutioning-agent" "$ds" >/dev/null
    say "created dataset $ds"
  fi
  sed "s/PROJECT/$PROJECT_ID/g; s/DATASET/$ds/g" bigquery/schema.sql \
    | bq --project_id="$PROJECT_ID" query --use_legacy_sql=false --quiet >/dev/null
  for col in "briefs deck_json STRING" "briefs research_brief STRING" "ingestion_threads received_at TIMESTAMP"; do
    set -- $col
    bq --project_id="$PROJECT_ID" query --use_legacy_sql=false --quiet \
      "ALTER TABLE \`$PROJECT_ID.$ds.$1\` ADD COLUMN IF NOT EXISTS $2 $3" >/dev/null
  done
done
say "BigQuery tables up to date"

# --- Pub/Sub: the build queue, its dead-letter topic, and the push to /work ---
for t in "$PUBSUB_TOPIC" "$PUBSUB_TOPIC-dead"; do
  gcloud pubsub topics describe "$t" $P >/dev/null 2>&1 \
    || { gcloud pubsub topics create "$t" $P --labels="$LABEL" >/dev/null; say "created topic $t"; }
done
if ! gcloud pubsub subscriptions describe "$PUBSUB_TOPIC-sub" $P >/dev/null 2>&1; then
  gcloud pubsub subscriptions create "$PUBSUB_TOPIC-sub" $P --labels="$LABEL" \
    --topic="$PUBSUB_TOPIC" --ack-deadline=600 \
    --push-endpoint="$PIPELINE_URL/work" --push-auth-service-account="$RUNTIME_SA" \
    --dead-letter-topic="$PUBSUB_TOPIC-dead" --max-delivery-attempts=5 \
    --min-retry-delay=60s --max-retry-delay=600s >/dev/null
  say "created subscription $PUBSUB_TOPIC-sub"
fi

# --- the inbox sweep: created paused; its pause state is never changed here ---
if gcloud scheduler jobs describe "$SCHEDULER_JOB" $P --location="$REGION" >/dev/null 2>&1; then
  gcloud scheduler jobs update http "$SCHEDULER_JOB" $P --location="$REGION" \
    --schedule="$SWEEP_SCHEDULE" --time-zone="Asia/Kolkata" >/dev/null
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" $P --location="$REGION" \
    --schedule="$SWEEP_SCHEDULE" --time-zone="Asia/Kolkata" --attempt-deadline=180s \
    --uri="$PIPELINE_URL/sweep" --http-method=POST \
    --oidc-service-account-email="$RUNTIME_SA" --oidc-token-audience="$PIPELINE_URL/sweep" >/dev/null
  gcloud scheduler jobs pause "$SCHEDULER_JOB" $P --location="$REGION" >/dev/null
  say "created $SCHEDULER_JOB (paused; resume it when inboxes should be read)"
fi

# --- evals in Cloud Build run as the runtime account and call the private renderer ---
gcloud run services add-iam-policy-binding "$RENDERER_SERVICE" $P --region="$REGION" \
  --member="serviceAccount:$RUNTIME_SA" --role=roles/run.invoker >/dev/null 2>&1 \
  || say "renderer not deployed yet; its invoker is granted on the next deploy"

# --- where eval results are kept ---
if ! gcloud storage buckets describe "gs://$EVAL_RESULTS_BUCKET" $P >/dev/null 2>&1; then
  gcloud storage buckets create "gs://$EVAL_RESULTS_BUCKET" $P --location=US \
    --uniform-bucket-level-access >/dev/null
  gcloud storage buckets update "gs://$EVAL_RESULTS_BUCKET" --update-labels="$LABEL" >/dev/null
  say "created bucket $EVAL_RESULTS_BUCKET"
fi

# --- the weekly full eval: a scheduler job that runs the "weekly evals" trigger ---
# The trigger itself is made once in the Cloud Build console (2nd gen, in
# $REGION), after the GitHub repository is connected
# (docs/operations/deploy.md); until then this waits.
TRIGGER="solutioning-agent-weekly-evals"
TRIGGER_ID=$(gcloud builds triggers describe "$TRIGGER" $P --region="$REGION" --format='value(id)' 2>/dev/null || true)
if [[ -n "$TRIGGER_ID" ]]; then
  URI="https://cloudbuild.googleapis.com/v1/projects/$PROJECT_ID/locations/$REGION/triggers/$TRIGGER_ID:run"
  if gcloud scheduler jobs describe "$TRIGGER" $P --location="$REGION" >/dev/null 2>&1; then
    gcloud scheduler jobs update http "$TRIGGER" $P --location="$REGION" \
      --schedule="$WEEKLY_EVAL_SCHEDULE" --time-zone="Asia/Kolkata" \
      --uri="$URI" --message-body='{}' >/dev/null
  else
    gcloud scheduler jobs create http "$TRIGGER" $P --location="$REGION" \
      --schedule="$WEEKLY_EVAL_SCHEDULE" --time-zone="Asia/Kolkata" \
      --uri="$URI" --http-method=POST --message-body='{}' \
      --oauth-service-account-email="$RUNTIME_SA" >/dev/null
    say "scheduled $TRIGGER ($WEEKLY_EVAL_SCHEDULE, India time)"
  fi
else
  say "weekly evals not scheduled yet: create the $TRIGGER trigger first"
fi

say "done"
