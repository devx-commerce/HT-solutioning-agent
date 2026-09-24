"""Self-serve Gmail onboarding — the pattern copied from commercial-context-layer-GE.

One URL, one click, per account manager. Nobody runs a script on anyone
else's behalf, and nothing here needs a Workspace admin: each person's own
"Allow" is the entire grant. What it produces — a per-user Secret Manager
secret holding their refresh token, and a row in BigQuery mapping their
email to that secret — is what the background poller reads from later with
no human present.

`access_type=offline` + `prompt=consent` together are what guarantee a
refresh_token comes back even on a repeat consent; drop either one and a
returning user silently gets no refresh_token at all.

The `hd` (hosted domain) check on the verified id_token is the entire
allowlist: only accounts on ALLOWED_ONBOARD_DOMAIN can onboard themselves.
That is deliberately coarser than the real project's eventual roster-Group
model — good enough for proving the mechanism, not a substitute for it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.parse
from datetime import datetime, timezone

import requests
from google.auth.transport import requests as google_requests
from google.cloud import bigquery, secretmanager
from google.oauth2 import id_token as google_id_token

GMAIL_SCOPES = [
    "openid",
    "email",
    "profile",
    # gmail.labels was tried first on the assumption it covers applying a
    # label to a message, same as it covers creating/listing labels. It
    # doesn't — messages.modify (used to actually attach a label id to a
    # message) 403s with "Insufficient Permission" under gmail.labels alone;
    # confirmed live 2026-09-24, first time this path was actually
    # exercised. gmail.modify is the narrowest scope that covers both label
    # management and applying a label — it also implies read access, so
    # gmail.readonly is redundant alongside it. Same as before: changing
    # the per-mailbox scope means already-onboarded mailboxes must
    # re-consent via /oauth/gmail/start.
    "https://www.googleapis.com/auth/gmail.modify",
]

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")
ALLOWED_DOMAIN = os.environ.get("ALLOWED_ONBOARD_DOMAIN", "")
STATE_SIGNING_KEY = os.environ.get("STATE_SIGNING_KEY", "")


def _client_material() -> dict:
    secret_name = os.environ["OAUTH_CLIENT_SECRET"]
    client = secretmanager.SecretManagerServiceClient()
    payload = client.access_secret_version(name=secret_name).payload.data
    return json.loads(payload.decode("utf-8"))


def _sign_state(redirect_uri: str) -> str:
    payload = f"{int(time.time())}.{redirect_uri}"
    sig = hmac.new(STATE_SIGNING_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _verify_state(state: str) -> str:
    # ts is always plain digits (no dots), so the first "." is a safe left
    # boundary. sig is a fixed-length hex digest (no dots), so the last "."
    # in the whole string is always the true boundary before it — even
    # though redirect_uri itself contains dots (e.g. "...run.app/...").
    ts, rest = state.split(".", 1)
    redirect_uri, sig = rest.rsplit(".", 1)
    expected = hmac.new(
        STATE_SIGNING_KEY.encode(), f"{ts}.{redirect_uri}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(sig, expected):
        raise ValueError("state signature mismatch — possible tampering")
    if time.time() - int(ts) > 600:
        raise ValueError("state expired — restart the onboarding flow")
    return redirect_uri


def build_auth_url(redirect_uri: str) -> str:
    """The URL an account manager visits to onboard their own mailbox."""
    material = _client_material()
    params = {
        "client_id": material["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GMAIL_SCOPES),
        "access_type": "offline",
        # select_account forces a chooser instead of silently reusing
        # whichever Google account is already active in the browser —
        # otherwise an AM with a personal Gmail session open could end up
        # authorizing the wrong account without ever seeing a picker.
        "prompt": "select_account consent",
        "state": _sign_state(redirect_uri),
    }
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)


def handle_callback(code: str, state: str) -> dict:
    """Exchange the code, verify who it is, store their refresh token."""
    redirect_uri = _verify_state(state)
    material = _client_material()

    token_resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": material["client_id"],
            "client_secret": material["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=10,
    ).json()

    if "refresh_token" not in token_resp:
        raise ValueError(
            "No refresh_token returned — this account already granted access "
            "before. Revoke it at myaccount.google.com/permissions and try again."
        )

    claims = google_id_token.verify_oauth2_token(
        token_resp["id_token"], google_requests.Request(), material["client_id"]
    )
    if not claims.get("email_verified"):
        raise ValueError("Email not verified on this Google account.")
    email = claims["email"]
    if ALLOWED_DOMAIN and claims.get("hd") != ALLOWED_DOMAIN:
        raise ValueError(f"Only @{ALLOWED_DOMAIN} accounts can onboard here.")

    secret_id = "gmail-" + hashlib.sha256(email.encode()).hexdigest()[:16]
    _store_refresh_token(secret_id, token_resp["refresh_token"])
    _upsert_user(email, secret_id)
    return {"email": email}


def _store_refresh_token(secret_id: str, refresh_token: str) -> None:
    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT}"
    secret_path = f"{parent}/secrets/{secret_id}"
    is_new = False
    try:
        client.get_secret(name=secret_path)
    except Exception:
        client.create_secret(
            parent=parent, secret_id=secret_id, secret={"replication": {"automatic": {}}}
        )
        is_new = True
    client.add_secret_version(parent=secret_path, payload={"data": refresh_token.encode()})

    if is_new:
        # A freshly created secret has no IAM bindings at all — not even
        # for the identity that just created it. Grant read access to
        # whichever identity is running right now, since in this
        # deployment both Cloud Run services (this one, and the private
        # one that later reads this secret via gmail_client.py) run as
        # the same service account. Missing this silently broke every
        # first sweep after onboarding — see docs/logs/ for the incident.
        # google.auth.default()'s credentials object doesn't reliably expose
        # the running service account's actual email — ask the metadata
        # server directly, the standard way to get it on Cloud Run/GCE.
        resp = requests.get(
            "http://metadata.google.internal/computeMetadata/v1/instance/"
            "service-accounts/default/email",
            headers={"Metadata-Flavor": "Google"},
            timeout=5,
        )
        principal = resp.text.strip()
        policy = client.get_iam_policy(request={"resource": secret_path})
        policy.bindings.add(
            role="roles/secretmanager.secretAccessor",
            members=[f"serviceAccount:{principal}"],
        )
        client.set_iam_policy(request={"resource": secret_path, "policy": policy})


def _upsert_user(email: str, secret_id: str) -> None:
    client = bigquery.Client(project=PROJECT)
    now = datetime.now(timezone.utc).isoformat()
    secret_ref = f"projects/{PROJECT}/secrets/{secret_id}/versions/latest"
    client.query(
        f"""
        MERGE `{PROJECT}.{DATASET}.users` T
        USING (SELECT @email AS email) S
        ON T.email = S.email
        WHEN MATCHED THEN UPDATE SET
          gmail_secret = @secret, status = 'active', updated_at = @now
        WHEN NOT MATCHED THEN
          INSERT (email, gmail_secret, status, onboarded_at, updated_at)
          VALUES (@email, @secret, 'active', @now, @now)
        """,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("email", "STRING", email),
                bigquery.ScalarQueryParameter("secret", "STRING", secret_ref),
                bigquery.ScalarQueryParameter("now", "STRING", now),
            ]
        ),
    ).result()
