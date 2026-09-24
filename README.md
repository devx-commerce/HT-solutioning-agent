# Solutioning Agent

Built component by component, against real infra from day one — every
permission and entitlement failure happens now, against the actual project,
not mid-timeline. The inbox poller, classification, and the solutioning briefs
sheet are real and running; deck generation is still a placeholder while
`docs/deck-agent-plan.md` gets built out as its own piece (superseded original design in `archive/deck-agent-v1/`).

Runs in **HT's GCP project** (`academic-diode-477405-m3`, confirmed inside
an organization — id `387062944839`, HT's own Cloud Identity). Auth is
OAuth, and **one identity does everything**: `sales.agent@hindustantimes.com`
is both the shared Drive/Slides identity and the first onboarded mailbox.
No `@devxlabs.ai` account needs to consent to anything — `navya.agarwal@
devxlabs.ai` only needs project IAM, not an OAuth grant.

## 0 — Access, before anything else

- **Org policy** isn't a blocker — `navya.agarwal@devxlabs.ai` already holds
  `roles/editor` on the project, granted by HT.
- **`roles/editor` never includes any `*.setIamPolicy`-shaped permission**,
  across every GCP service. Test the specific permission string you need
  directly rather than assuming a broad role covers it:
  ```bash
  curl -s -X POST -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    "https://cloudresourcemanager.googleapis.com/v1/projects/academic-diode-477405-m3:testIamPermissions" \
    -d '{"permissions":["the.permission.you.need"]}'
  ```

Point-in-time record — see [`docs/logs/`](docs/logs/) for the dated ground
truth on what's actually granted.

## 1 — APIs

```bash
gcloud config set project academic-diode-477405-m3

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

**Two OAuth clients (different flow shapes), one identity behind both.**
`sales.agent@hindustantimes.com` bootstraps the shared Drive/Slides identity
*and* is the first self-onboarded mailbox — there's still a Desktop client
and a Web application client because the flows themselves differ (installed
-app vs. browser-redirect), not because two different people are involved.

In HT's project, APIs & Services → OAuth consent screen:

- **User type: Internal.** The project is confirmed inside HT's own
  organization, and the only identity that will ever consent is
  `@hindustantimes.com` — Internal mode fits exactly, with none of
  External+Testing's downsides: no test-user list to maintain, no 7-day
  refresh-token expiry, no unverified-app warning screen.
- Scopes across both clients (current as of 2026-09-23 — this list has
  drifted before, cross-check against `app/auth/gmail_oauth.py`'s `GMAIL_SCOPES` and
  `app/auth/oauth_creds.py`'s `SCOPES` if it looks stale again):
  - **Web application client** (per-user, `app/auth/gmail_oauth.py`): `openid`,
    `email`, `profile`, `gmail.readonly`, `gmail.labels`.
  - **Desktop client** (shared identity, `app/auth/oauth_creds.py`): `drive` (not
    `drive.file` — that scope only sees files the app itself created,
    which 404s trying to copy a manually-created template; confirmed live
    2026-09-23), `presentations`, `gmail.send`, `spreadsheets`,
    `gmail.readonly`, `gmail.labels` (the last two are deliberately also on
    the shared identity today, not just the per-mailbox one — see
    `app/auth/oauth_creds.py`'s module docstring for why).
  - Both `gmail.readonly`/`gmail.labels` and `drive` are sensitive/restricted
    scopes: requesting them in code is not enough — they must also be
    explicitly added under **Google Auth Platform → Data access** in the
    console, or Google silently drops them from the consent grant instead
    of erroring, which only surfaces later as a confusing `invalid_scope` on
    token refresh or a 404 on API calls that assume the scope is there.

Then Credentials → Create OAuth client ID, **twice**:

1. **Desktop app** → download `client_secret.json`. Used once, in step 3,
   logged in as `sales.agent@hindustantimes.com`, to bootstrap the shared
   Drive/Slides identity.
2. **Web application** → add `https://<your-cloud-run-url>/oauth/gmail/callback`
   as an authorized redirect URI (you'll know the URL after step 6, so come
   back and add it then — Google rejects any callback whose URI wasn't
   pre-registered). `sales.agent@hindustantimes.com` visits this flow too,
   in step 6b, to onboard itself as the first mailbox the poller watches.

## 3 — Bootstrap the shared Drive/Slides identity

```bash
pip install -r requirements.txt
python scripts/get_refresh_token.py path/to/client_secret.json
```

Walks you through the consent screen once — **log in as
`sales.agent@hindustantimes.com`** when the browser opens, using the
**Desktop app** client — and prints a JSON blob. Store it:

```bash
echo -n '<paste the JSON blob>' | gcloud secrets create solutioning-agent-oauth \
  --data-file=- --replication-policy=automatic
```

Also create the client-credentials secret the **Web application** client's
onboarding flow uses (client id/secret only, no per-user data):

```bash
echo -n '{"client_id":"...","client_secret":"..."}' | \
  gcloud secrets create solutioning-agent-oauth-client \
  --data-file=- --replication-policy=automatic
```

## 4 — BigQuery

```bash
bq mk --dataset academic-diode-477405-m3:solutioning_agent

sed "s/PROJECT/academic-diode-477405-m3/; s/DATASET/solutioning_agent/" \
  bigquery/schema.sql | bq query --use_legacy_sql=false
```

Six tables: `briefs`, `decisions`, `users`, `sweep_state` (the watermark),
`ingestion_threads` (thread-level dedup), and a generic `audit_log` (merged
2026-09-23 from three originally-separate, unused tables — `handoffs`,
`retrieval_log`, and a narrower `audit_log` — into one flexible
`event_type` + JSON `detail` shape; nothing writes to it yet). See
[`docs/EMAIL-POLLER-DESIGN.md`](docs/EMAIL-POLLER-DESIGN.md) for why
`sweep_state`/`ingestion_threads` exist.

## 5 — Deck rendering, and the solutioning briefs sheet

There is no Slides template to create — HT doesn't have one. Decks are
rendered from Deck JSON by `presentation-md` and uploaded to Drive, which
converts the pptx to native Slides. Point `PRESENTATION_MD_CLI` at that
repo's `packages/renderer-node/dist/cli.js`, and set `DECK_READER_DOMAIN` so
generated decks are readable by the domain. See
[`docs/research-and-rendering-decisions.md`](docs/research-and-rendering-decisions.md)
— including the unresolved question of where Node runs once deployed.

Create the solutioning briefs Sheet by hand,
header row matching the nine columns in
[`docs/EMAIL-POLLER-DESIGN.md`](docs/EMAIL-POLLER-DESIGN.md), share it with
`sales.agent@hindustantimes.com` as an Editor, and copy its id into
`BRIEFS_SHEET_ID`. The agent only ever appends rows to it — nothing here
creates or formats the sheet itself.

## 6 — Cloud Run

Two Cloud Run services from one source tree, gated by a `PUBLIC_ROUTES_ONLY`
env var checked in `app/main.py`:

- **`solutioning-agent`** (private, `--no-allow-unauthenticated`) — the
  operational service: `/status`, `/sweep`, `/work`, `/handle_message`.
- **`solutioning-agent-onboarding`** (public, `--allow-unauthenticated`) —
  hosts only `/healthz` and `/oauth/gmail/*`, since `/oauth/gmail/start`
  needs to be reachable by an account manager's browser with no prior auth.

Deploy the private one first:

```bash
gcloud run deploy solutioning-agent \
  --source=. \
  --region=us-central1 \
  --no-allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=academic-diode-477405-m3,GOOGLE_CLOUD_LOCATION=us-central1,BQ_DATASET=solutioning_agent,OAUTH_TOKEN_SECRET=projects/296974829876/secrets/solutioning-agent-oauth/versions/latest,OAUTH_CLIENT_SECRET=projects/296974829876/secrets/solutioning-agent-oauth-client/versions/latest,STATE_SIGNING_KEY=YOUR_RANDOM_KEY,ALLOWED_ONBOARD_DOMAIN=hindustantimes.com,BRIEFS_SHEET_ID=YOUR_SHEET_ID,CLASSIFY_MODEL=gemini-2.0-flash-lite,BUILD_WORK_TOPIC=solutioning-agent-build-work,SOLUTIONING_NOTIFY_EMAIL=sales.agent@hindustantimes.com"
```

Then the public onboarding one, same source, `PUBLIC_ROUTES_ONLY=true` added
and `--allow-unauthenticated` instead:

```bash
gcloud run deploy solutioning-agent-onboarding \
  --source=. \
  --region=us-central1 \
  --allow-unauthenticated \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=academic-diode-477405-m3,GOOGLE_CLOUD_LOCATION=us-central1,BQ_DATASET=solutioning_agent,OAUTH_TOKEN_SECRET=projects/296974829876/secrets/solutioning-agent-oauth/versions/latest,OAUTH_CLIENT_SECRET=projects/296974829876/secrets/solutioning-agent-oauth-client/versions/latest,STATE_SIGNING_KEY=YOUR_RANDOM_KEY,ALLOWED_ONBOARD_DOMAIN=hindustantimes.com,PUBLIC_ROUTES_ONLY=true"
```

**The onboarding service's `--allow-unauthenticated` flag alone is not
always enough** — its IAM policy can still lack the actual `allUsers` /
`roles/run.invoker` binding (deploy output shows `Setting IAM policy
failed` when this happens). Confirm and fix directly if so:
```bash
gcloud run services add-iam-policy-binding solutioning-agent-onboarding \
  --region=us-central1 --member="allUsers" --role="roles/run.invoker"
```
Its runtime service account also needs `secretmanager.secretAccessor` on
both `solutioning-agent-oauth` and `solutioning-agent-oauth-client` — a
separate grant from whatever the private service already has, since they
may not share a service account.

(`AGENT_ENGINE_RESOURCE` and `SERVICE_URL` aren't known yet — this step's
own output produces the URLs, step 9 produces the resource name. Step 6b and
step 11 below update the *private* service once each exists; the public
onboarding service only ever needs its own `SERVICE_URL`, pointed at
itself.)

## 6b — Register the callback URL and set SERVICE_URL

```bash
URL=$(gcloud run services describe solutioning-agent --region=us-central1 --format='value(status.url)')
echo "$URL"
```

Go back to the **Web application** OAuth client from step 2 and add
`${URL}/oauth/gmail/callback` as an authorized redirect URI — the callback
fails before your code runs if this doesn't match exactly. Then:

```bash
gcloud run services update solutioning-agent --region=us-central1 \
  --set-env-vars="SERVICE_URL=${URL}"
```

Smoke test — `/work` is now the Pub/Sub push target (step 7), not a
standalone check, so confirm BigQuery access via `/status` instead, which
needs nothing onboarded yet to return a (empty) result:

```bash
TOKEN=$(gcloud auth print-identity-token)
curl -H "Authorization: Bearer $TOKEN" "$URL/status"
```

`{"total": 0, ...}` rather than an error means Cloud Run's runtime identity
→ BigQuery works — one of the two riskiest permission paths, proven. The
other — Gmail — isn't testable yet, because no mailbox is onboarded. That's
the next step:
visit `$URL/oauth/gmail/start` in a browser (not curl — it's a redirect a
human follows) and **consent as `sales.agent@hindustantimes.com`**. `/sweep`
will only find a mailbox once at least one account has done this.

Check who's actually onboarded at any point with:
```bash
curl -H "Authorization: Bearer $TOKEN" "$URL/status"
```
This is the first thing to check when someone says "my emails aren't being
picked up" — it separates "never onboarded" from "onboarded but token
needs refreshing" (`reauthorization_required`) without digging into logs.

## 7 — Pub/Sub

No longer optional — `/sweep` publishes build-work rather than invoking
Agent Engine inline, specifically to bound how many agent sessions can run
concurrently. The load here (~100 emails/week) is low on average but not
uniformly spread, so a burst day could otherwise fire many Agent Engine
sessions at once from one `/sweep` call; a queue plus a capped consumer
controls that independent of how many are queued. See
[`docs/EMAIL-POLLER-DESIGN.md`](docs/EMAIL-POLLER-DESIGN.md).

**Create the subscription before anything ever publishes to the topic** —
a message published to a topic with no subscription yet is simply dropped,
not queued.

```bash
gcloud pubsub topics create solutioning-agent-build-work
gcloud pubsub topics create solutioning-agent-build-work-dead
gcloud pubsub subscriptions create solutioning-agent-build-work-sub \
  --topic=solutioning-agent-build-work \
  --push-endpoint="$URL/work" \
  --push-auth-service-account=SA_EMAIL \
  --dead-letter-topic=solutioning-agent-build-work-dead \
  --max-delivery-attempts=5
```

Then bound actual concurrency on the Cloud Run service itself — this, not
Pub/Sub, is what caps how many `/work` invocations (and therefore Agent
Engine sessions) run at once:

```bash
gcloud run services update solutioning-agent --region=us-central1 \
  --concurrency=1 --max-instances=3
```

`--concurrency=1` means one build per container instance; `--max-instances`
is the real dial for "how many agent instances can run concurrently" — 3 is
a reasonable starting point for this load, tune once you've seen a real
burst day.

## 8 — Cloud Scheduler

Create it, then **pause it** — nothing needs to run unattended yet.

```bash
gcloud scheduler jobs create http solutioning-agent-sweep \
  --location=us-central1 \
  --schedule="*/30 * * * *" \
  --uri="$URL/sweep" \
  --oidc-service-account-email=SA_EMAIL \
  --http-method=POST

gcloud scheduler jobs pause solutioning-agent-sweep
```

## 9 — Agent Engine

```bash
adk deploy agent_engine \
  --project=academic-diode-477405-m3 \
  --region=us-central1 \
  --display_name="Solutioning Agent" \
  --description="Builds a first-draft solution deck for an inbound advertising request." \
  agents/solutioning_agent
```

`--staging_bucket` and `--trace_to_cloud` are gone from this command
deliberately — both are deprecated in current `adk`, and `--trace_to_cloud`
specifically **breaks the deploy outright** (confirmed 2026-09-23): it needs
an OpenTelemetry Cloud Trace exporter package that isn't in
`requirements.txt`, so the container builds and pushes fine and then
crashes on every startup with `ModuleNotFoundError:
opentelemetry.exporter.cloud_trace`. Use `--otel_to_cloud` instead if you
want tracing back, and add the matching exporter package first.

This is also the first point where you find out whether the Gemini model is
actually enabled with quota in this project and region — a real thing to
learn now.

**Every `adk deploy agent_engine` run creates a brand-new
`reasoningEngines/<ID>` resource — it does not update the existing one in
place.** After any redeploy (including one with no code changes, e.g. just
to force a fresh process), you must re-wire the new ID in two places: step
11's `AGENT_ENGINE_RESOURCE` env var on the private Cloud Run service, and
GE's own agent registration (Details tab → edit the `reasoningEngines/...`
path). Forgetting the second one means GE keeps calling the old, stale
deployment and you'll be debugging an error that's already fixed.

**Secret rotation doesn't take effect on a warm, already-running instance.**
`app/auth/oauth_creds.py`'s `_token_material()` is `@lru_cache`d — it reads Secret
Manager's `latest` version once per process and never again. Rotating a
secret (e.g. re-minting a refresh token) has no effect on an Agent Engine
instance that was already warm before the rotation; only a fresh process
(a redeploy, or a cold start) will pick up the new value. If a fix that
should have worked doesn't, and the previous step was a secret rotation
rather than a code change, redeploy before debugging further.

## 10 — Register in Gemini Enterprise

GE app → Agents → Add agent → Custom agent via Agent Runtime → paste the
`reasoningEngines/...` resource name from step 9.

Grant the invoker role GE needs to call it:

```bash
gcloud ai reasoning-engines add-iam-policy-binding RESOURCE_ID \
  --region=us-central1 \
  --member="serviceAccount:service-296974829876@gcp-sa-discoveryengine.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"
```

**This is the command step 0 flagged as likely to fail** — `roles/editor`
was confirmed missing `aiplatform.reasoningEngines.setIamPolicy`. If it
errors with a permission-denied here, that's expected, not a new problem:
get `roles/aiplatform.admin` added, or have someone who already has it run
this one command.

Ask it in chat: "build a deck for Test Client." A real, openable deck link
back means the entire chain — GE → Agent Runtime → Agent Engine → OAuth
creds → Drive → Slides — works, in HT's project, before the real business
rules exist to build on top of it.

## 11 — Wire the resource name back in, then test all three flows

```bash
gcloud run services update solutioning-agent --region=us-central1 \
  --set-env-vars="AGENT_ENGINE_RESOURCE=projects/296974829876/locations/us-central1/reasoningEngines/RESOURCE_ID"
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

**Rare, and person-specific — not a timer.** Because the consent screen is
Internal from the start (step 2), there's no 7-day unverified-app expiry to
worry about here at all. A token only dies if someone actually revokes it
(at `myaccount.google.com/permissions`) or a Workspace admin revokes the
app org-wide — real events, not a background clock.

**Re-onboarding needs no separate flow.** `/oauth/gmail/start` is the same
link for a first-time consent and a repeat one — the callback's `MERGE`
updates the existing row rather than duplicating it. There is no "reauth
mode" to build.

**What was missing was telling anyone.** `/sweep` catches a dead token per
user (`mark_reauthorization_required`), and now also emails that person the
same onboarding link via `app/pipeline/notifications.py` — sent from the shared system
identity, never from another account manager's mailbox. It fires exactly
once per break: a user who's `reauthorization_required` drops out of
`active_users()` until they re-consent, so the sweep can't re-notify them on
every subsequent poll. Check `/status` any time to see who's overdue without
waiting for the next sweep.

## What this deliberately does not do

No research, no pricing. Classification and Sheet population are now
built — see below — but reply-triggered refinement, a real Category
taxonomy, and an AM/GH lookup table are still deliberately deferred.

## The ingestion pipeline: classification, threads, the briefs sheet

Full design and reasoning in
[`docs/EMAIL-POLLER-DESIGN.md`](docs/EMAIL-POLLER-DESIGN.md) —
`app/pipeline/ingestion.py` is its implementation, not a second copy of the same
decisions. Three things worth knowing before running it:

- **Two Gmail queries per sweep, not one.** Branch A is watermark-bounded
  (`storage.get_sweep_cutoff`); Branch B (`label:solutioning-agent/generate-deck`)
  is never time-bounded, on purpose.
- **Adding `gmail.labels` to the per-mailbox scope (step 2) means already-
  onboarded mailboxes need to re-consent** — visit `/oauth/gmail/start`
  again for each one already onboarded under the old readonly-only grant.
- **`solutioning-agent/deck-generated` is the only thing that ever visibly
  touches a mailbox.** A rejection, a thread already handled, insufficient
  content on a manually-flagged email — none of these label or mark-read
  anything. If `/status` and a mailbox's actual label list disagree about
  what's been processed, that's expected: the real record is
  `decisions`/`ingestion_threads` in BigQuery, not Gmail's visible state.

## The next layer: deck generation

Today's `build_solution_deck` is a placeholder — a `drive.files.copy` +
`replaceAllText` on a template deck. The real plan, decided 2026-09-23, is
**not** to build a Slides-API compiler from scratch: fork and patch
`presentation-md` instead, since it already clears the bar (real editable
text/tables, a native OOXML chart object, a working theme system across 73
themes). That fork is developed entirely as its own separate project at
`~/Desktop/codebase/presentation-md/` — not inside this repo, not a
submodule — and only enters `HT-solutioning-agent` later, as a consumed
package/build artifact once it's tested and fixed.

Full plan: [`docs/deck-agent-plan.md`](docs/deck-agent-plan.md)
— confirmed bugs found in the fork so far, the fix list, what to keep/cut
from its packages, and the testing plan.

The original build-from-scratch design is archived in
[`archive/deck-agent-v1/`](archive/deck-agent-v1/) — superseded for how the
deck gets rendered, but its README notes two pieces still worth reading
(the scope-bug prediction in `10-risks.md` §1, and the locking/ownership
model in `08-lifecycle.md`).
