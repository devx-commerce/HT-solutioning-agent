"""A deliberate duplicate of the repo-root oauth_creds.py — not a fork.

`adk deploy agent_engine` only bundles the agent's own directory; nothing
outside `agents/solutioning_agent/` makes it into the deployed package, and
there's no flag to include extra local packages (checked directly against
`adk deploy agent_engine --help`). The repo-root copy stays where it is
because Cloud Run's notifications.py and sheet.py need it there too — two
different deployment targets, two different packaging scopes, one
credential underneath both.

If the scope list or the secret-reading logic ever changes, change it in
both places. Nothing enforces that automatically today.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

SCOPES = [
    # drive.file only grants access to files this app created or the user
    # explicitly picked via a file picker — the template deck was created
    # manually, outside this app, so drive.file 404s trying to copy it.
    # Full drive scope is needed to read/copy pre-existing files.
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
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
