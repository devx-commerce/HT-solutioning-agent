"""The two Cloud Run services this same source deploys as.

One codebase, two deployments, split by what must be public vs. what must
not be — not by IAM configuration on a single shared surface:

  solutioning-agent            --no-allow-unauthenticated (native Cloud Run
                                IAM). Hosts /sweep, /work, /status,
                                /handle_message. Called by Cloud Scheduler,
                                Pub/Sub, and you (via your own gcloud
                                identity) — never by an anonymous request.

  solutioning-agent-onboarding --allow-unauthenticated, PUBLIC_ROUTES_ONLY=true.
                                Hosts only /healthz and /oauth/gmail/*. A
                                browser and Google's own redirect can't
                                present a Cloud Run invoker identity, so
                                these two routes have no choice but to be
                                public — deliberately isolated onto a
                                service that exposes nothing else, so a bug
                                here can't reach anything sensitive.

PUBLIC_ROUTES_ONLY controls which routes actually get registered — on the
onboarding deployment, /sweep etc. don't just go unprotected, they don't
exist at all (a request to them 404s). This is the "no way to get it wrong"
property that made us choose two services over one service with an in-code
auth check: see developer-docs/EMAIL-POLLER-DESIGN.md for the reasoning.

Two identity models still live side by side underneath both:
  - Drive/Slides: one shared identity (oauth_creds.py), bootstrapped once.
  - Gmail: per-account-manager, self-onboarded via /oauth/gmail/*.
"""

from __future__ import annotations

import base64
import json
import logging
import os

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from google.cloud import bigquery

from .auth import gmail_oauth
from .auth.gmail_client import active_users, get_service_for_user, mark_reauthorization_required
from .pipeline import agent_client, ingestion, mail_utils
from .pipeline.notifications import send_reauth_prompt

app = FastAPI()
log = logging.getLogger("solutioning_agent")

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")
PUBLIC_ROUTES_ONLY = os.environ.get("PUBLIC_ROUTES_ONLY", "false").lower() == "true"


def healthz() -> dict:
    return {"ok": True}


# --- Gmail onboarding — public on both deployments, meaningful on neither
# except the onboarding one, since only that one has a registered redirect
# URI and a reachable SERVICE_URL matching it. ------------------------------


def oauth_start() -> RedirectResponse:
    """Visit this URL to onboard your own mailbox. No script, no admin grant."""
    redirect_uri = f"{_self_url()}/oauth/gmail/callback"
    return RedirectResponse(gmail_oauth.build_auth_url(redirect_uri))


def oauth_callback(code: str, state: str) -> HTMLResponse:
    try:
        result = gmail_oauth.handle_callback(code, state)
    except ValueError as exc:
        return HTMLResponse(f"<p>Onboarding failed: {exc}</p>", status_code=400)
    return HTMLResponse(f"<p>Onboarded {result['email']}. You can close this tab.</p>")


def _self_url() -> str:
    # Set to *this* deployment's own URL post-deploy — must exactly match a
    # redirect URI registered on the OAuth client, or Google rejects the
    # callback before your code ever runs.
    return os.environ.get("SERVICE_URL", "http://localhost:8080")


# --- everything below is private-service-only -------------------------------


def status() -> dict:
    """Who's onboarded, and whose token needs attention.

    Returns emails and status only — never gmail_secret, since that's a
    Secret Manager resource path and this endpoint has no reason to expose
    even the path to it. Cross-check against /sweep's per-user errors when
    someone reports "my emails aren't being picked up."
    """
    client = bigquery.Client(project=PROJECT)
    rows = client.query(
        f"SELECT email, status, onboarded_at, updated_at "
        f"FROM `{PROJECT}.{DATASET}.users` ORDER BY onboarded_at DESC"
    ).result()
    users = [dict(row) for row in rows]
    return {
        "total": len(users),
        "active": sum(1 for u in users if u["status"] == "active"),
        "needs_reauth": sum(1 for u in users if u["status"] == "reauthorization_required"),
        "users": users,
    }


def sweep(force: bool = False, dry_run: bool = False) -> dict:
    """Run the ingestion pipeline against every onboarded mailbox.

    See developer-docs/EMAIL-POLLER-DESIGN.md and ingestion.py for the actual logic —
    watermark-bounded classification (Branch A) plus the always-included
    manual-label override (Branch B). A stale/revoked token degrades that
    one user's row (mark_reauthorization_required) rather than failing the
    whole sweep — same posture as the real project's per-source envelope
    degradation.

    Two testing flags, both off by default and meant to be used from a
    terminal, not by Cloud Scheduler:

      POST /sweep?force=true    reprocess every candidate as if seen for
                                 the first time — ignores the watermark and
                                 prior decisions. Never touches the real
                                 watermark. Expect duplicate rows on a
                                 mailbox already swept normally.
      POST /sweep?dry_run=true  skip the actual Agent Engine call, verify
                                 everything else (classification, labels,
                                 the sheet row) without a deployed agent.

    Combine as POST /sweep?force=true&dry_run=true to replay the same test
    inbox repeatedly while iterating on the classifier or the sheet output.
    """
    results = []
    for user in active_users():
        try:
            gmail = get_service_for_user(user["gmail_secret"])
            results.append(
                ingestion.run_sweep_for_user(
                    user["email"], gmail, user["gmail_secret"],
                    force=force, dry_run=dry_run,
                )
            )
        except Exception as exc:
            # extra={} fields don't reach Cloud Logging's textPayload with
            # this app's plain logging.getLogger setup (no structured/JSON
            # handler configured) — they were silently swallowed, which cost
            # real debugging time tracking down a genuine classify.py bug
            # tonight. Put the error in the message itself so it's always
            # visible regardless of handler config.
            log.warning(f"sweep.user_failed email={user['email']} error={exc!r}")
            mark_reauthorization_required(user["email"])
            # Fires exactly once per break: active_users() only ever returns
            # status='active' rows, so this user drops out of the next sweep
            # until they re-onboard and reset it — no separate dedup needed.
            try:
                send_reauth_prompt(user["email"])
            except Exception as notify_exc:
                log.error(
                    "sweep.reauth_notify_failed",
                    extra={"email": user["email"], "error": str(notify_exc)},
                )
            results.append({"email": user["email"], "error": "reauthorization_required"})
    return {"users_checked": len(results), "results": results}


def work(envelope: dict) -> dict:
    """Pub/Sub push target — one build task, one Agent Engine invocation.

    Cloud Run's own --max-instances / --concurrency on this service is what
    actually bounds how many of these run at once — Pub/Sub delivers, it
    doesn't limit concurrency by itself. See pubsub.py.
    """
    data = envelope.get("message", {}).get("data", "")
    if not data:
        return {"ok": False, "reason": "no Pub/Sub message data in envelope"}

    payload = json.loads(base64.b64decode(data).decode("utf-8"))
    outcome = ingestion.execute_build(payload)
    return {"ok": outcome == "built", "outcome": outcome, "thread_id": payload.get("thread_id")}


def handle_message(email: str, message_id: str) -> dict:
    """Flow 1, manual entry point: one onboarded mailbox, one message, by hand.

    Superseded for normal operation by /sweep's ingestion pipeline — this
    stays as a direct way to test the agent-invocation path against one
    specific message without going through classification at all.
    """
    user_row = next((u for u in active_users() if u["email"] == email), None)
    if user_row is None:
        return {"ok": False, "reason": f"{email} is not onboarded — visit /oauth/gmail/start"}

    gmail = get_service_for_user(user_row["gmail_secret"])
    msg = mail_utils.fetch_message(gmail, message_id)
    if not msg.body:
        return {"ok": False, "reason": "no plain-text body found"}

    reply = agent_client.invoke_agent(
        f"An email came in. Subject: {msg.subject}\n\nBody:\n{msg.body}\n\n"
        "Build a placeholder solution deck for whoever this is from."
    )

    log.info("handle_message.done", extra={"email": email, "message_id": message_id})
    return {"ok": True, "agent_reply": reply}


# --- route registration ------------------------------------------------------
# Always present, on both deployments:
app.add_api_route("/healthz", healthz, methods=["GET"])
app.add_api_route("/oauth/gmail/start", oauth_start, methods=["GET"])
app.add_api_route("/oauth/gmail/callback", oauth_callback, methods=["GET"])

# Only on the private deployment. On the onboarding deployment
# (PUBLIC_ROUTES_ONLY=true) these simply don't exist — a request to them
# 404s, rather than being merely unprotected.
if not PUBLIC_ROUTES_ONLY:
    app.add_api_route("/status", status, methods=["GET"])
    app.add_api_route("/sweep", sweep, methods=["POST"])
    app.add_api_route("/work", work, methods=["POST"])
    app.add_api_route("/handle_message/{email}/{message_id}", handle_message, methods=["POST"])
