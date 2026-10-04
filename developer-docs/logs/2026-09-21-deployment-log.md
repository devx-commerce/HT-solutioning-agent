# 2026-09-21 — Solutioning Agent deployment log

Full log of everything done today getting the Solutioning Agent live
against HT's real GCP project (`academic-diode-477405-m3`), end to end:
rename, deployment, six bugs fixed, Gemini Enterprise registration, and
the access gaps that are still blocking full operation.

## 1. Rename: pitch agent → solutioning agent

Renamed throughout — code, infra resource naming, and the local repo
directory (`HT-sales-pitch-agent` → `HT-solutioning-agent`). "Pitch
deck" → "solution deck" everywhere in code and docs.

- **GitHub repo renamed** — confirmed live: `devx-commerce/HT-solutioning-agent`
  returns HTTP 200, old name `HT-sales-pitch-agent` returns HTTP 301
  (GitHub's standard rename redirect). Local `origin` remote already
  points at the new name. Done, nothing outstanding here.

## 2. Real deployment against HT's GCP project

Stood up the full stack for real, not just locally:

- OAuth consent screen + OAuth clients (hit Netskope corruption here —
  see §4)
- BigQuery (dataset + `briefs` table, used by `lookup_deck` /
  `build_solution_deck` / `update_deck`)
- Cloud Run — split into **two services** deliberately:
  - `solutioning-agent-onboarding` — public-facing, handles OAuth
    onboarding (`/oauth/gmail/start` etc.)
  - `solutioning-agent` (private) — the operational service: inbox
    sweep, classification, deck drafting trigger
  - Justified against a sibling POC (`commercial-context-layer-GE`) and
    against real Cloud Run IAM models (native
    `--allow-unauthenticated`/`--no-allow-unauthenticated` vs. in-code
    OIDC verification) rather than assumed — this split was checked, not
    just carried over from habit.
  - Route registration gated by a `PUBLIC_ROUTES_ONLY` env var so the
    same codebase can serve either role depending on which service it's
    deployed as.
- Pub/Sub (push subscription feeding the private service)
- Cloud Scheduler (`solutioning-agent-sweep` job — polls the inbox on a
  schedule)
- Vertex AI Agent Engine (the ADK reasoning engine itself)
- Secret Manager (OAuth refresh tokens, template file IDs, etc.)

### Six deployment bugs fixed to get the agent live and tool-calling

(Full detail lives in earlier session history / commit history — noting
here that this was six distinct, sequential real bugs, not one. Worth
pulling the specifics from `git log` if needed later rather than
re-deriving from memory.)

### Packaging constraint discovered

`adk deploy agent_engine` only bundles the agent's own directory
(`agents/solutioning_agent/`) — there is no `--extra_packages` flag.
This forced deliberate duplication of `oauth_creds.py` into the agent's
own package (with a comment flagging it as a duplicate-on-purpose, since
Cloud Run's `notifications.py`/`sheet.py` need the repo-root copy too).
Same reasoning applied to `tools/deck.py`, which lives inside the agent
package for the same reason.

## 3. Netskope diagnosis

HT's Netskope CASB reverse-proxy was found to be corrupting Google OAuth
traffic — first surfaced as broken/malformed `client_id` values during
the OAuth consent flow, later confirmed again today as URL truncation
(`.com` stripped from `auth.cloud.google.com` → `auth.cloud.google`,
same for `vertexaisearch.cloud.google.com`) when previewing the agent in
GE as my own account.

- A **scoped Netskope exception** was obtained for
  `sales.agent@hindustantimes.com` specifically — not a blanket fix for
  all `*.cloud.google.com` traffic. This is why the truncation
  reappeared today under my own account: the exception only ever
  covered that one service account, not general browsing.
- HT's SSO IdP is AD FS (`adfs3.htmedia.in`), relevant context for why
  this proxy sits in the auth path at all.
- **Workaround** for hitting this as myself: skip GE's "Preview" button
  (which round-trips through `auth.cloud.google.com`) and navigate
  directly to the app's own chat URL
  (`vertexaisearch.cloud.google.com/us/home/cid/<app-id>`) — works
  because the browser session is already authenticated, so it never
  needs the corrupted redirect.

## 4. Gemini Enterprise (GE) registration

Registered the agent in the GE console today — the last untried step
in the "front-load every blocker now" pass:

- App: `HT-sales-pitch-agent` → Agents → **Custom agent via Agent
  Runtime**.
- **Authorizations step**: skipped. This step is for GE brokering
  per-user OAuth consent to an external API — not needed here, since
  the agent already authenticates itself via its own fixed identity
  (`sales.agent@hindustantimes.com`'s refresh token, stored in Secret
  Manager, read by the reasoning engine's runtime service identity).
  Adding an authorization here would have been a redundant second OAuth
  layer.
- **Configuration step**: agent name "Solutioning Agent", description,
  and the reasoning engine resource path:
  `projects/296974829876/locations/us-central1/reasoningEngines/1985844993356464128`.
- **Result: created successfully, shows Enabled.** This answered a real
  open question — registration itself needed no IAM beyond what was
  already granted. The permission gap only shows up one step later, at
  user-access / invocation time (see §5).

### Confirmed: Gmail onboarding for sales.agent@hindustantimes.com

Verified via Secret Manager rather than assumed:
- `solutioning-agent-oauth` — version 1, enabled, created
  2026-09-21T12:30:48
- `solutioning-agent-oauth-client` — version 1, enabled, created
  2026-09-21T12:31:14

Both fresh today, confirming `/oauth/gmail/start` was run and completed
by `sales.agent@hindustantimes.com` specifically (confirmed directly)
without Netskope corrupting the flow — consistent with that account's
scoped exception working as intended. Done.

## 5. IAM permission gaps — the running pattern

Recurring theme across the entire deployment: **`roles/editor` never
includes any `*.setIamPolicy`-shaped permission**, across every service
touched. Each service's IAM-setting permission had to be discovered
independently, by hitting the actual error:

| Gap found | Where it surfaced |
|---|---|
| `run.admin` | Cloud Run service IAM (public/private bindings) |
| `iam.serviceAccountUser` | Binding service accounts to Pub/Sub, Scheduler, Cloud Run |
| `resourcemanager.projectIamAdmin` (or scoped BigQuery fallback) | BigQuery dataset access for the reasoning-engine identity |
| `secretmanager.admin` (or secret-scoped fallback) | Secret Manager access for the reasoning-engine identity |
| `aiplatform.admin` | Vertex AI reasoning engine invoker binding |
| `discoveryengine.agents.setIamPolicy` (new today) | Adding self as a permissioned user on the GE agent, so it shows up in GE chat |

Rather than sending these to HT IT/admin piecemeal as each was
discovered, the deliberate call was to **front-load discovery of every
remaining blocker first**, then send one consolidated request. Today's
GE registration attempt was specifically done to find the last unknown
item on this list before sending anything.

### Access request — final form, most-encompassing → most fine-grained

Ask in this order; fall to the next tier only if refused:

1. **`roles/owner`** (project-level) — covers everything below in one
   grant. Hardest to get approved; only lead with this if expecting a
   fast yes.
2. **`roles/iam.securityAdmin`** (project-level, alongside existing
   Editor) — **the recommended ask.** Grants `setIamPolicy`/
   `getIamPolicy` across project resources without billing, deletion, or
   IAM-role-creation powers. Should resolve every row in the table above
   in one grant, without the blast radius of Owner.
3. **Fallback — named resource-level roles**, if #2 is refused:
   - `roles/run.admin`
   - `roles/aiplatform.admin` (status unconfirmed — admin claimed
     granted earlier, not yet re-verified against a real action)
   - `roles/discoveryengine.admin` (new, found today)
   - `roles/secretmanager.admin` (fallback: scope to just the
     `solutioning-agent-oauth*` secrets if pushed back on)
   - `roles/iam.serviceAccountUser`
4. **Most fine-grained fallback** — ask IT to run the specific
   `setIamPolicy` calls themselves per resource, rather than granting
   any standing role. Slowest for future changes, easiest single "yes"
   for a security-conscious admin.

## Open questions

- Was `aiplatform.admin` actually granted? Admin claimed yes; not yet
  re-verified against a real action (e.g. invoking the reasoning engine
  directly, or the GE agent's user-permissions call).
- Once `iam.securityAdmin` (or the fallback roles) lands, does it
  actually resolve `discoveryengine.agents.setIamPolicy`, or does GE
  need its own separate grant regardless? Untested — confirm on first
  retry rather than assuming.

## Still blocked / not yet done

- Add self as a permissioned user on Solutioning Agent in GE, so it
  shows up in the GE chat app for interactive testing.
- Make `solutioning-agent-onboarding` Cloud Run service actually public
  (blocked on `run.admin`).
- Wire Pub/Sub push-auth + Cloud Scheduler OIDC invoker binding on the
  private `solutioning-agent` service (blocked on `run.admin`).
- Resume the `solutioning-agent-sweep` Scheduler job — **deliberately
  left paused**; do not resume until the private service's auth
  bindings above are actually working, or it will fire against a
  broken/unauthenticated endpoint.
- Full end-to-end retest once the above lands: `lookup_deck` →
  `build_solution_deck`, run live from a real GE chat session (not just
  the reasoning-engine playground).
- Send the consolidated access request above to HT IT/admin — drafted,
  not yet sent.
