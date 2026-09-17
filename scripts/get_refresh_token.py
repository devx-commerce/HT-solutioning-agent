"""Run this once, locally, to bootstrap the ONE shared identity.

This is Drive/Slides + system-notification-send only — never a mailbox to
poll. Not part of the deployed service; this is the one step that needs a
human and a browser. Opens a local server, walks through Google's consent
screen, and prints the JSON blob to paste into Secret Manager as
OAUTH_TOKEN_SECRET. Per-account-manager Gmail reading is a completely
separate, self-serve flow — see gmail_oauth.py — not this script.

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
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/presentations",
    "https://www.googleapis.com/auth/gmail.send",
]


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/get_refresh_token.py client_secret.json")

    flow = InstalledAppFlow.from_client_secrets_file(sys.argv[1], SCOPES)
    creds = flow.run_local_server(port=0)

    material = {
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "refresh_token": creds.refresh_token,
    }
    print("\nPaste this into Secret Manager (do not commit it anywhere):\n")
    print(json.dumps(material, indent=2))


if __name__ == "__main__":
    main()
