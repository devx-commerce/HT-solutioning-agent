"""Per-user Gmail access — the read side of self-serve onboarding.

Every onboarded account manager gets their own Gmail client, built from
their own stored refresh token. One shared OAuth client id/secret across
everyone (from OAUTH_CLIENT_SECRET); only the refresh token differs per
person, which is exactly the part gmail_oauth.py stored per-user.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from google.auth.transport.requests import Request
from google.cloud import bigquery, secretmanager
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from .gmail_oauth import GMAIL_SCOPES

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")


@lru_cache(maxsize=1)
def _client_material() -> dict:
    secret_name = os.environ["OAUTH_CLIENT_SECRET"]
    client = secretmanager.SecretManagerServiceClient()
    payload = client.access_secret_version(name=secret_name).payload.data
    return json.loads(payload.decode("utf-8"))


def active_users() -> list[dict]:
    """Every mailbox the background poller should check this run."""
    client = bigquery.Client(project=PROJECT)
    rows = client.query(
        f"SELECT email, gmail_secret FROM `{PROJECT}.{DATASET}.users` "
        f"WHERE status = 'active'"
    ).result()
    return [dict(row) for row in rows]


def get_service_for_user(gmail_secret: str):
    """A Gmail API client authenticated as one onboarded account manager."""
    material = _client_material()
    secret_client = secretmanager.SecretManagerServiceClient()
    refresh_token = secret_client.access_secret_version(name=gmail_secret).payload.data.decode(
        "utf-8"
    )
    creds = Credentials(
        token=None,
        refresh_token=refresh_token,
        client_id=material["client_id"],
        client_secret=material["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=GMAIL_SCOPES,
    )
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds)


def mark_reauthorization_required(email: str) -> None:
    """A stored refresh token stopped working — flip status so a person notices.

    Mirrors commercial-context-layer-GE's gmail_ingestion behaviour: a
    refresh failure for one mailbox degrades that one row, not the sweep.
    """
    client = bigquery.Client(project=PROJECT)
    client.query(
        f"UPDATE `{PROJECT}.{DATASET}.users` SET status = 'reauthorization_required' "
        f"WHERE email = @email",
        job_config=bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("email", "STRING", email)]
        ),
    ).result()
