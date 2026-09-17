"""The one shared identity: Drive + Slides, plus system-level Gmail send.

This is deliberately NOT where per-mailbox Gmail *reading* lives — that's
inherently per-person and lives in gmail_oauth.py / gmail_client.py, onboarded
by each account manager themselves. This identity is the opposite: one
trusted, admin-bootstrapped credential (scripts/get_refresh_token.py) that
never impersonates an AM and never reads anyone's mailbox. gmail.send is
here only for the agent to notify the system's own operators — e.g. "please
reauthorize" — never to email a client or an AM's contacts. That mirrors the
real project's separate `solutioning_delivery` principal: sending capability
kept apart from any personal mailbox, deliberately.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/gmail.send",
]


@lru_cache(maxsize=1)
def _token_material() -> dict:
    secret_name = os.environ["OAUTH_TOKEN_SECRET"]
    from google.cloud import secretmanager

    client = secretmanager.SecretManagerServiceClient()
    payload = client.access_secret_version(name=secret_name).payload.data
    return json.loads(payload.decode("utf-8"))


def get_credentials() -> Credentials:
    material = _token_material()
    creds = Credentials(
        token=None,
        refresh_token=material["refresh_token"],
        client_id=material["client_id"],
        client_secret=material["client_secret"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    creds.refresh(Request())
    return creds
