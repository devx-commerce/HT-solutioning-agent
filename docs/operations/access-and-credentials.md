# Access and credentials

## The agent's own account

The agent works as **sales.agent@hindustantimes.com**. That account owns the
decks, sends the "Solution deck drafted" emails, reads the past decks folder
and searches it through Gemini Enterprise. It must:

- stay active in HT's Google Workspace;
- keep a **Gemini Enterprise licence**: without one, every past-decks search
  fails with "User must be assigned a license" and drafts say no prior HT
  work was found. Licences are assigned in Gemini Enterprise > Manage users;
- keep access to the "Past Pitch Decks" folder (shared by the solutioning
  team).

Its access is a refresh token stored in the secret `solutioning-agent-oauth`,
as JSON: `{"client_id": "...", "client_secret": "...", "refresh_token": "..."}`.

### Renewing the agent's token

Needed only if the token is revoked (for example the account's password is
reset by IT, or access is removed in its Google account settings). Symptoms:
every build fails, and the logs show `invalid_grant`.

1. In Cloud Console > APIs & Services > Credentials, download the desktop
   OAuth client's JSON (or create a Desktop app client).
2. On a computer with Python, run:
   `python scripts/get_refresh_token.py path/to/client_secret.json`
   and sign in as sales.agent@hindustantimes.com.
3. Store the printed JSON as a new version of the secret:
   `gcloud secrets versions add solutioning-agent-oauth --data-file=token.json`
4. Deploy (or wait for the next deploy): running services read the secret
   when they start.

## People's inboxes

Each person connects their own inbox through the onboarding page
(`https://solutioning-agent.hindustantimes.com/oauth/gmail/start`, the short
address of `https://solutioning-agent-onboarding-296974829876.us-central1.run.app`).
After they allow access, Google returns them to the run.app address, because
that is the redirect URI registered on the OAuth client; the short address
needs no entry there.
Their access is stored in a secret named `gmail-<16 characters>`, and they
appear in the BigQuery table `solutioning_agent.users`.

The pipeline must be able to read every one of those secrets. Grant that once
per project, for all `gmail-` secrets present and future:

```bash
gcloud projects add-iam-policy-binding academic-diode-477405-m3 \
  --member=serviceAccount:296974829876-compute@developer.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor \
  --condition='expression=resource.name.startsWith("projects/296974829876/secrets/gmail-"),title=solutioning-agent-inboxes'
```

Without it, a newly connected inbox is never read, and the pipeline's logs
show `sweep.inbox_failed` for it every check.

- **Who may connect:** accounts on `settings.onboarding_domains`.
- **Access doesn't expire on a timer.** It stops only if the person revokes it,
  or IT removes it. The next inbox check then marks them
  `reauthorization_required` and emails them a reconnect link (the same
  onboarding link). They reconnect in under a minute.
- **To stop reading someone's inbox:** they revoke "Solutioning Agent" at
  myaccount.google.com/permissions, or you set their row in
  `solutioning_agent.users` to a status other than `active`.

## The other secrets

| Secret | How to create or replace it |
|---|---|
| `solutioning-agent-oauth-client` | JSON `{"client_id": "...", "client_secret": "..."}` of the **web** OAuth client the onboarding page uses. Its authorised redirect URI must be `https://solutioning-agent-onboarding-296974829876.us-central1.run.app/oauth/gmail/callback`. |
| `solutioning-agent-state-key` | Any long random string; it signs onboarding links. Replacing it only invalidates links someone is part-way through. |
| `solutioning-agent-youtube-key` | A YouTube Data API v3 key, restricted to that API. |

When creating any of these secrets, or `solutioning-agent-oauth`, let the
deploy and the services read it:

```bash
gcloud secrets add-iam-policy-binding <name> \
  --member=serviceAccount:296974829876-compute@developer.gserviceaccount.com \
  --role=roles/secretmanager.secretAccessor
```

Add a new version with `gcloud secrets versions add <name> --data-file=<file>`.
Never disable the latest version to "roll back": Secret Manager still serves
the newest version even when it is disabled. Add a new version instead.

## The deploy's own access

Deploys and eval runs both run in Cloud Build as the compute service
account. Besides reading the secrets above, it signs itself in to the private
deck renderer during evals, which needs this once per project:

```bash
SA=296974829876-compute@developer.gserviceaccount.com
gcloud iam service-accounts add-iam-policy-binding $SA \
  --member=serviceAccount:$SA --role=roles/iam.serviceAccountOpenIdTokenCreator
```

Without it the deploy still succeeds, but every eval run in Cloud Build reports
"the rendering service is unavailable" and fails its deck check.

## Workspace admin settings this depends on

- The OAuth consent screen is **Internal** to HT's Workspace, which is why
  people's access doesn't expire weekly.
- HT's Workspace admin has marked the Gemini Enterprise Drive connector's
  OAuth client as trusted.
- HT's network proxy (Netskope) has in the past broken Google sign-in
  pages. If connecting an inbox fails with a garbled Google error, try from a
  network or browser outside the proxy and report it to IT.
