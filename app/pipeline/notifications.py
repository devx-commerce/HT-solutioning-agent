"""Emails the poller sends as itself — never as, or to, an external party.

Two things live here, both sent from the one shared system identity, never
from an onboarded mailbox: a reauth prompt (the part re-onboarding itself
was missing), and the deck-built notification. Neither is a reply on a
client thread — this build's scope is solutioning's own inbox, and the
notification goes back to solutioning, not out to whoever the triggering
email came from. That's what makes `send` (not just `draft`) safe here:
nothing in this module ever addresses an external recipient.
"""

from __future__ import annotations

import base64
import email.mime.text
import os

from googleapiclient.discovery import build

from ..auth.oauth_creds import get_credentials

SERVICE_NAME = "Solutioning Agent"


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


def send_deck_notification(client_name: str | None, brief: str | None) -> None:
    """One new email to solutioning's own notify address — never a reply
    on the triggering thread, which may have external participants.

    Placeholder body for now; once deck generation exists, this carries
    the deck link and the evidence it was built from instead.
    """
    to = os.environ.get("SOLUTIONING_NOTIFY_EMAIL", "")
    if not to:
        raise RuntimeError("SOLUTIONING_NOTIFY_EMAIL is not set — nowhere to send this.")

    body = (
        "A solution deck has been drafted.\n\n"
        f"Client: {client_name or '(not extracted)'}\n"
        f"Brief: {brief or '(not extracted)'}\n\n"
        "[placeholder — the deck link and the evidence it was built from "
        "will appear here once deck generation is wired up]"
    )
    message = email.mime.text.MIMEText(body)
    message["to"] = to
    message["subject"] = f"Solution deck drafted — {client_name or 'unspecified client'}"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    gmail = build("gmail", "v1", credentials=get_credentials())
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()
