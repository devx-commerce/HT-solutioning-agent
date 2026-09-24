"""Run this once, locally, to bootstrap the shared identity.

Not part of the deployed service; this is the one step that needs a human
and a browser. Opens a local server, walks through Google's consent screen,
and prints the JSON blob to paste into Secret Manager as OAUTH_TOKEN_SECRET.

Currently scoped for Drive/Slides/Sheets/send *and* Gmail reading — covering
the one mailbox we have today without needing the separate self-serve web
flow (gmail_oauth.py) at all. See oauth_creds.py's docstring for why.

Usage:
    python scripts/get_refresh_token.py path/to/client_secret.json

client_secret.json comes from the OAuth client you create in the HT project's
Cloud Console (APIs & Services > Credentials > Create OAuth client ID >
Desktop app). Requires the consent screen to be in Testing mode with your
account added as a test user — see the README for why.
"""

from __future__ import annotations

import json
import sys

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.labels",
]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/get_refresh_token.py client_secret.json")

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    # prompt=select_account: without this, Google silently uses whichever
    # Google account is already active in the browser instead of asking —
    # a real problem when the machine running this also has a personal or
    # devxlabs.ai session logged in, since it'll try to authorize as that
    # one instead of the intended shared identity and fail with org_internal.
    creds = flow.run_local_server(port=0, prompt="select_account")

    material = {
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    print("\nPaste this into Secret Manager (do not commit it anywhere):\n")
    print(json.dumps(material, indent=2))


if __name__ == "__main__":
    main()
