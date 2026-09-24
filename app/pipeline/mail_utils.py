"""Pulling the parts of a Gmail message both /handle_message and ingestion
need — subject, plain-text body, thread id, arrival time. No parsing beyond
that; field extraction is classify.py's job, not this module's.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class ParsedMessage:
    message_id: str
    thread_id: str
    subject: str
    body: str
    received_at: datetime
    sender: str
    rfc_message_id: str  # the Message-ID *header*, for In-Reply-To/References —
    # not the Gmail-internal message_id above, a different id entirely


def _walk_for_plain_text(part) -> str:
    if part.get("mimeType") == "text/plain" and "data" in part.get("body", {}):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "replace")
    for sub in part.get("parts", []) or []:
        found = _walk_for_plain_text(sub)
        if found:
            return found
    return ""


def fetch_message(gmail, message_id: str) -> ParsedMessage:
    msg = gmail.users().messages().get(userId="me", id=message_id, format="full").execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    received_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
    return ParsedMessage(
        message_id=message_id,
        thread_id=msg["threadId"],
        subject=headers.get("Subject", ""),
        body=_walk_for_plain_text(msg["payload"]),
        received_at=received_at,
        sender=headers.get("From", ""),
        rfc_message_id=headers.get("Message-ID", ""),
    )


def fetch_thread_context(gmail, thread_id: str) -> str:
    """The whole thread's content, oldest first, one block per message.

    A thread can carry its real substance in a later reply (budget,
    timeline, a scope change) rather than the message that happened to
    trigger classification — classify against the whole conversation, not
    just whichever single message tripped the filter.
    """
    thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
    blocks = []
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("From", "")
        date = headers.get("Date", "")
        body = _walk_for_plain_text(msg["payload"])
        blocks.append(f"From: {sender}\nDate: {date}\n\n{body}")
    return "\n\n---\n\n".join(blocks)
