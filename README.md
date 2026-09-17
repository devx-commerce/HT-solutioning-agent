# Pitch Agent — deployment skeleton

No pitch logic. Every piece of real infra, hollow behind it, so the
permission and entitlement failures happen now — against a project you don't
have build pressure on yet — instead of mid-timeline.

Runs in **HT's GCP project**, not a devxlabs one. Auth is OAuth (one
consenting user, refresh token in Secret Manager).

## 0 — Access, before anything else

Two things to confirm with HT, in this order, because either can block
everything below:

1. **Org policy.** Does HT's project have a domain-restricted-sharing policy
   (`iam.allowedPolicyMemberDomains`)? If so, `@devxlabs.ai` identities can't
   be granted *any* IAM role until HT adds an exception or issues you
   accounts in their own domain. Ask literally: "can devxlabs.ai identities
   hold IAM roles on this project, or do you need to allow that first?"
2. **GE entitlement.** Confirm HT's Gemini Enterprise licence actually
   includes registering a **custom agent via Agent Runtime** — not just
   using GE's built-in agents. This is a product entitlement, not a
   permission; no IAM role fixes it if the tier doesn't include it.

Once cleared, get these IAM roles granted on HT's project to your
`@devxlabs.ai` account:

```
roles/run.admin
roles/bigquery.dataEditor          (project or dataset level)
roles/bigquery.dataOwner           (on the dataset, to create tables)
roles/secretmanager.admin
roles/aiplatform.user
roles/discoveryengine.agentspaceAdmin
roles/pubsub.editor
roles/cloudscheduler.admin
```

## 1 — APIs

```bash
gcloud config set project HT_PROJECT_ID

gcloud services enable \
  aiplatform.googleapis.com \
  discoveryengine.googleapis.com \
  gmail.googleapis.com \
  drive.googleapis.com \
  slides.googleapis.com \
  secretmanager.googleapis.com \
  pubsub.googleapis.com \
  run.googleapis.com \
  cloudscheduler.googleapis.com \
  cloudtrace.googleapis.com \
  bigquery.googleapis.com \
  cloudbuild.googleapis.com
```

`discoveryengine.googleapis.com` is the one people forget — GE registration
fails without it.

## 2 — OAuth consent screen + two clients (console, not gcloud)

**Two separate identity models, two OAuth clients.** Several individual
mailboxes means Gmail has to be per-account-manager, self-onboarded — the
pattern copied from `commercial-context-layer-GE`. Drive/Slides stays one
shared identity, since a deck lives in one place regardless of which
mailbox produced the brief.

In HT's project, APIs & Services → OAuth consent screen:

- **User type: External.**
- **Publishing status: Testing.** Add every onboarding account (starting
  with `you@devxlabs.ai`) as a test user. (Internal mode would restrict
  this to `@hindustantimes.com` accounts only — it would reject a devxlabs
  account outright, not just warn. Switch to Internal once real HT mailboxes
  are the only ones onboarding, and this per-test-user list goes away.)
- Scopes across both clients: `openid`, `email`, `profile`, `gmail.readonly`
  (per-user, Web application client), `drive.file`, `presentations`,
  `gmail.send` (shared identity, Desktop client — send is for the system to
  notify its own operators, e.g. "please reauthorize," never to email a
  client or another person's contacts).

Then Credentials → Create OAuth client ID, **twice**:

1. **Desktop app** → download `client_secret.json`. Used once, locally, in
   step 3, to bootstrap the one shared Drive/Slides identity.
2. **Web application** → add `https://<your-cloud-run-url>/oauth/gmail/callback`
   as an authorized redirect URI (you'll know the URL after step 6, so come
   back and add it then — Google rejects any callback whose URI wasn't
   pre-registered). This is the client every account manager's browser talks
   to when they self-onboard.

Testing-mode refresh tokens on these scopes expire after ~7 days of the app
being unverified — expected re-auth cadence for now, not a bug.

## 3 — Bootstrap the shared Drive/Slides identity

```bash
pip install -r requirements.txt
python scripts/get_refresh_token.py path/to/client_secret.json
```

Walks you through the consent screen once (using the **Desktop app**
client), prints a JSON blob. Store it:

```bash
echo -n '<paste the JSON blob>' | gcloud secrets create pitch-agent-oauth \
  --data-file=- --replication-policy=automatic
```

Also create the client-credentials secret the **Web application** client's
onboarding flow uses (client id/secret only, no per-user data):

```bash
echo -n '{"client_id":"...","client_secret":"..."}' | \
  gcloud secrets create pitch-agent-oauth-client \
  --data-file=- --replication-policy=automatic
```

## 4 — BigQuery

```bash
bq mk --dataset HT_PROJECT_ID:pitch_agent_skeleton

sed "s/PROJECT/HT_PROJECT_ID/; s/DATASET/pitch_agent_skeleton/" \
  bigquery/schema.sql | bq query --use_legacy_sql=false
```

## 5 — Drive template

Create one Slides file with `{{CLIENT_NAME}}` and `{{NOTES}}` placeholders,
owned by whoever ran step 3. Copy its file id into `TEMPLATE_FILE_ID`.

## 6 — Cloud Run

```bash
gcloud run deploy pitch-agent-skeleton \
  --source=. \
  --region=us-central1 \
  --no-allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=HT_PROJECT_ID,GOOGLE_CLOUD_LOCATION=us-central1,BQ_DATASET=pitch_agent_skeleton,OAUTH_TOKEN_SECRET=projects/HT_PROJECT_NUMBER/secrets/pitch-agent-oauth/versions/latest,OAUTH_CLIENT_SECRET=projects/HT_PROJECT_NUMBER/secrets/pitch-agent-oauth-client/versions/latest,STATE_SIGNING_KEY=YOUR_RANDOM_KEY,ALLOWED_ONBOARD_DOMAIN="
```

(`AGENT_ENGINE_RESOURCE` and `SERVICE_URL` aren't known yet — this step's
own output produces the URL, step 9 produces the resource name. Step 6b and
step 11 below update this service once each exists.)

## 6b — Register the callback URL and set SERVICE_URL

```bash
URL=$(gcloud run services describe pitch-agent-skeleton --region=us-central1 --format='value(status.url)')
echo "$URL"
```

Go back to the **Web application** OAuth client from step 2 and add
`${URL}/oauth/gmail/callback` as an authorized redirect URI — the callback
fails before your code runs if this doesn't match exactly. Then:

```bash
gcloud run services update pitch-agent-skeleton --region=us-central1 \
  --set-env-vars="SERVICE_URL=${URL}"
```

Smoke test:

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer $TOKEN" -X POST "$URL/work"
```

`{"ok": true}` means Cloud Run's runtime identity → BigQuery works — one of
the two riskiest permission paths, proven. The other — Gmail — isn't
testable yet, because no mailbox is onboarded. That's the next step:
visit `$URL/oauth/gmail/start` in a browser (not curl — it's a redirect a
human follows) and consent as yourself. `/sweep` will only find a mailbox
once at least one person has done this.

Check who's actually onboarded at any point with:
```bash
curl -H "Authorization: Bearer $TOKEN" "$URL/status"
```
This is the first thing to check when someone says "my emails aren't being
picked up" — it separates "never onboarded" from "onboarded but token
needs refreshing" (`reauthorization_required`) without digging into logs.

## 7 — Pub/Sub (optional at skeleton stage)

Only stand this up once 6 works — it's not needed to prove BigQuery or
Gmail access, only the fan-out shape:

```bash
gcloud pubsub topics create pitch-agent-work
gcloud pubsub topics create pitch-agent-work-dead
gcloud pubsub subscriptions create pitch-agent-work-sub \
  --topic=pitch-agent-work \
  --push-endpoint="$URL/work" \
  --push-auth-service-account=SA_EMAIL \
  --dead-letter-topic=pitch-agent-work-dead \
  --max-delivery-attempts=5
```

## 8 — Cloud Scheduler

Create it, then **pause it** — nothing needs to run unattended yet.

```bash
gcloud scheduler jobs create http pitch-agent-sweep \
  --location=us-central1 \
  --schedule="*/15 * * * *" \
  --uri="$URL/sweep" \
  --oidc-service-account-email=SA_EMAIL \
  --http-method=POST

gcloud scheduler jobs pause pitch-agent-sweep
```

## 9 — Agent Engine

```bash
gcloud storage buckets create gs://HT_PROJECT_ID-agent-staging --location=us-central1

adk deploy agent_engine \
  --project=HT_PROJECT_ID \
  --region=us-central1 \
  --staging_bucket=gs://HT_PROJECT_ID-agent-staging \
  --display_name="Pitch Agent (skeleton)" \
  --description="Deployment skeleton — builds a placeholder deck only." \
  --trace_to_cloud \
  agents/pitch_agent_hello
```

This is also the first point where you find out whether the Gemini model is
actually enabled with quota in this project and region — a real thing to
learn now.

## 10 — Register in Gemini Enterprise

GE app → Agents → Add agent → Custom agent via Agent Runtime → paste the
`reasoningEngines/...` resource name from step 9.

Grant the invoker role GE needs to call it:

```bash
gcloud ai reasoning-engines add-iam-policy-binding RESOURCE_ID \
  --region=us-central1 \
  --member="serviceAccount:service-HT_PROJECT_NUMBER@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

Ask it in chat: "build a deck for Test Client." A real, openable deck link
back means the entire chain — GE → Agent Runtime → Agent Engine → OAuth
creds → Drive → Slides — works, in HT's project, before the real business
rules exist to build on top of it.

## 11 — Wire the resource name back in, then test all three flows

```bash
gcloud run services update pitch-agent-skeleton --region=us-central1 \
  --set-env-vars="AGENT_ENGINE_RESOURCE=projects/HT_PROJECT_NUMBER/locations/us-central1/reasoningEngines/RESOURCE_ID"
```

**Flow 2 (chat), test this one first — it needs nothing from Cloud Run:**
In the GE app, paste a fake email body and ask for a deck. A real link back
proves GE → Agent Runtime → Agent Engine → OAuth → Drive/Slides end to end.

**Flow 1 (email-triggered):**
```bash
curl -H "Authorization: Bearer $TOKEN" -X POST "$URL/sweep"
# pick an (email, message_id) pair from the response, then:
curl -H "Authorization: Bearer $TOKEN" -X POST "$URL/handle_message/EMAIL/MESSAGE_ID"
```
The response carries the agent's reply and deck link directly — it doesn't
email it back to you, because the per-user Gmail scope is `readonly` only
(matching commercial-context-layer-GE's pattern). Sending would mean adding
`gmail.send` to the onboarding scopes and a send call in `handle_message`;
deliberately left out for now since read-only is the smaller thing to have
gotten wrong. `/sweep` and `/handle_message` are also not wired together
yet — that join, plus Pub/Sub in between, is what step 7 adds once this
works by hand.

**Flow 3 (refinement), in the same GE chat session:**
"Change the client name on that deck to Acme Corp" — the agent should call
`lookup_deck` then `update_deck` rather than building a new one. Then open a
**fresh** GE chat session and ask it to edit the same deck by name — this is
the case that actually exercises `lookup_deck` (a continuing session doesn't
need it, since ADK already remembers the deck id in its own state).

## Re-authorization: how often, and what happens

**Weekly, for everyone, until the OAuth consent screen leaves Testing mode.**
That's a hard 7-day cap Google puts on unverified apps' refresh tokens —
nothing in this code causes it and nothing in this code can extend it. Once
the screen is Internal (restricted to HT's own org) or fully verified, this
stops being a timer and becomes a rare, person-specific event instead —
someone revokes access themselves, or a Workspace admin does.

**Re-onboarding needs no separate flow.** `/oauth/gmail/start` is the same
link for a first-time consent and a repeat one — the callback's `MERGE`
updates the existing row rather than duplicating it. There is no "reauth
mode" to build.

**What was missing was telling anyone.** `/sweep` catches a dead token per
user (`mark_reauthorization_required`), and now also emails that person the
same onboarding link via `notifications.py` — sent from the shared system
identity, never from another account manager's mailbox. It fires exactly
once per break: a user who's `reauthorization_required` drops out of
`active_users()` until they re-consent, so the sweep can't re-notify them on
every subsequent poll. Check `/status` any time to see who's overdue without
waiting for the next sweep.

## What this deliberately does not do

No classification, no research, no email sending, no pricing, no BigQuery
schema beyond the shape. All of that is the next layer, once HT's actual
rules land — this exists only to answer "does the infra work" first.
