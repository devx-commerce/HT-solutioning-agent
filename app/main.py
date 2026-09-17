"""The Cloud Run service: Gmail onboarding, sweep, and flow 1's handler.

Two identity models live side by side on purpose:
  - Drive/Slides: one shared identity (oauth_creds.py), bootstrapped once.
  - Gmail: per-account-manager, self-onboarded via /oauth/gmail/*, because
    that's the one thing that has to be per-person once there's more than
    one mailbox.
"""

from __future__ import annotations

import base64
import logging
import os
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.responses import HTMLResponse, RedirectResponse
from google.cloud import bigquery

import gmail_oauth
from gmail_client import active_users, get_service_for_user, mark_reauthorization_required
from notifications import send_reauth_prompt

app = FastAPI()
log = logging.getLogger("pitch_agent_skeleton")

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
DATASET = os.environ.get("BQ_DATASET", "pitch_agent_skeleton")
AGENT_ENGINE_RESOURCE = os.environ.get("AGENT_ENGINE_RESOURCE", "")

_BASE_QUERY = "is:unread in:inbox -category:promotions -category:social -in:chats"


@app.get("/healthz")
def healthz() -> dict:
    return {"ok": True}


# --- Gmail onboarding ------------------------------------------------------


@app.get("/oauth/gmail/start")
def oauth_start() -> RedirectResponse:
    """Visit this URL to onboard your own mailbox. No script, no admin grant."""
    redirect_uri = f"{_self_url()}/oauth/gmail/callback"
    return RedirectResponse(gmail_oauth.build_auth_url(redirect_uri))


@app.get("/oauth/gmail/callback")
def oauth_callback(code: str, state: str) -> HTMLResponse:
    try:
        result = gmail_oauth.handle_callback(code, state)
    except ValueError as exc:
        return HTMLResponse(f"<p>Onboarding failed: {exc}</p>", status_code=400)
    return HTMLResponse(f"<p>Onboarded {result['email']}. You can close this tab.</p>")


def _self_url() -> str:
    # Set to the Cloud Run service's own URL post-deploy (README step 6b) —
    # it must exactly match a redirect URI registered on the OAuth client,
    # or Google rejects the callback before your code ever runs.
    return os.environ.get("SERVICE_URL", "http://localhost:8080")


@app.get("/status")
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


# --- background watcher ----------------------------------------------------


@app.post("/sweep")
def sweep() -> dict:
    """Check every onboarded mailbox for messages matching the tier-1 filter.

    A stale/revoked token degrades that one user's row (mark_reauthorization_
    required) rather than failing the whole sweep — same posture as the real
    project's per-source envelope degradation.
    """
    results = []
    for user in active_users():
        try:
            gmail = get_service_for_user(user["gmail_secret"])
            resp = gmail.users().messages().list(userId="me", q=_BASE_QUERY).execute()
            ids = [m["id"] for m in resp.get("messages", [])]
            results.append({"email": user["email"], "message_ids": ids})
        except Exception as exc:
            log.warning("sweep.user_failed", extra={"email": user["email"], "error": str(exc)})
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


@app.post("/work")
def work() -> dict:
    """Write one dummy brief row. Proves: Cloud Run runtime identity -> BigQuery."""
    client = bigquery.Client(project=PROJECT)
    table = f"{PROJECT}.{DATASET}.briefs"
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "brief_id": f"hollow-{now}",
        "message_id": None,
        "client_name": "skeleton-test",
        "status": "hollow",
        "attempts": 0,
        "detail": "written by /work with no real classification behind it",
        "deck_file_id": None,
        "deck_link": None,
        "created_at": now,
        "updated_at": now,
    }
    errors = client.insert_rows_json(table, [row])
    if errors:
        log.error("work.bigquery_write_failed", extra={"errors": errors})
        return {"ok": False, "errors": errors}
    return {"ok": True, "row": row}


def _extract_body(gmail, message_id: str) -> tuple[str, str]:
    """Subject and plain-text body of one message — no further parsing here."""
    msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    subject = headers.get("Subject", "")

    def _walk(part) -> str:
        if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            found = _walk(sub)
            if found:
                return found
        return ""

    return subject, _walk(msg["payload"])


def _invoke_agent(text: str) -> str:
    """Call the deployed Agent Engine resource directly — not through GE.

    Verify against your installed google-cloud-aiplatform's actual
    `vertexai.agent_engines` signature before assuming a failure here is a
    permissions problem — this API surface has moved across versions.
    """
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=PROJECT, location=LOCATION)
    engine = agent_engines.get(AGENT_ENGINE_RESOURCE)
    session = engine.create_session(user_id="skeleton-flow1")
    reply_text = ""
    for event in engine.stream_query(
        user_id="skeleton-flow1", session_id=session["id"], message=text
    ):
        reply_text += str(event)
    return reply_text


@app.post("/handle_message/{email}/{message_id}")
def handle_message(email: str, message_id: str) -> dict:
    """Flow 1, end to end, for one onboarded mailbox and one message.

    Not wired to /sweep automatically — call /sweep, pick an (email,
    message_id) pair from its response, call this by hand. That join is
    Pub/Sub's job once this path is proven.
    """
    user_row = next((u for u in active_users() if u["email"] == email), None)
    if user_row is None:
        return {"ok": False, "reason": f"{email} is not onboarded — visit /oauth/gmail/start"}

    gmail = get_service_for_user(user_row["gmail_secret"])
    subject, body = _extract_body(gmail, message_id)
    if not body:
        return {"ok": False, "reason": "no plain-text body found"}

    reply = _invoke_agent(
        f"An email came in. Subject: {subject}\n\nBody:\n{body}\n\n"
        "Build a placeholder pitch deck for whoever this is from."
    )

    log.info("handle_message.done", extra={"email": email, "message_id": message_id})
    return {"ok": True, "agent_reply": reply}
