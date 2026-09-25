"""The one shared identity — currently covering everything, deliberately.

gmail_oauth.py / gmail_client.py's self-serve per-AM onboarding flow exists
for a scale we don't have yet: several people onboarding themselves without
a developer doing it for them. Today there's exactly one mailbox
(sales.agent@) and one person bootstrapping it — that's what
scripts/get_refresh_token.py already does, locally, with no public endpoint
needed. So for now this identity also holds the Gmail read scopes, and the
`users` table gets seeded by hand pointing at this same secret rather than
going through /oauth/gmail/start at all. When a second mailbox actually
needs onboarding, that's the point to revisit whether the self-serve flow
(already built, just unused) is worth it — not before.
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
    # Added temporarily so this one identity can also cover Gmail reading
    # for the single mailbox we have today — see module docstring.
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
]
# Searching the past-decks data store additionally needs cloud-platform, but
# adding it here before the stored token has been re-minted with that scope
# makes every refresh fail with invalid_scope. Add it and re-mint together —
# see docs/open-items.md.


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
