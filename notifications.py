"""Telling a person their token died — the part re-onboarding was missing.

The re-onboarding mechanism itself needed nothing new: /oauth/gmail/start
already upserts on a repeat consent. What was missing was anyone finding out
they needed to visit it. This sends one email, from the shared system
identity (never from another account manager's mailbox), with the same link
that new onboarding uses — there is no separate "reauth" URL.
"""

from __future__ import annotations

import base64
import email.mime.text
import os

from googleapiclient.discovery import build

from oauth_creds import get_credentials

SERVICE_NAME = "Pitch Agent (skeleton)"


def send_reauth_prompt(email_address: str) -> None:
    onboarding_url = f"{os.environ.get('SERVICE_URL', '')}/oauth/gmail/start"

    body = (
        f"{SERVICE_NAME} can no longer read your inbox — your access needs to "
        f"be renewed.\n\n"
        f"Click here to reconnect (takes 10 seconds, same as the first time):\n"
        f"{onboarding_url}\n\n"
        f"Nothing else has changed. This happens automatically about once a "
        f"week while this is still in early testing, and should stop once it "
        f"moves out of testing mode."
    )
    message = email.mime.text.MIMEText(body)
    message["to"] = email_address
    message["subject"] = f"{SERVICE_NAME}: please reconnect your inbox"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    gmail = build("gmail", "v1", credentials=get_credentials())
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
