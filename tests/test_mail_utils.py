"""Tests for mail_utils, focused on fetch_thread_context (added 2026-09-25
so classification and the build prompt see a whole conversation, not just
whichever single message tripped the filter first)."""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

from app.pipeline import mail_utils


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _fake_gmail_message(sender: str, date: str, body: str) -> dict:
    return {
        "payload": {
            "headers": [{"name": "From", "value": sender}, {"name": "Date", "value": date}],
            "mimeType": "text/plain",
            "body": {"data": _b64(body)},
        }
    }


def test_fetch_thread_context_concatenates_all_messages_in_order():
    gmail = MagicMock()
    gmail.users().threads().get().execute.return_value = {
        "messages": [
            _fake_gmail_message("client@example.com", "Mon, 1 Sep 2026 10:00:00", "Initial ask, thin on detail."),
            _fake_gmail_message("client@example.com", "Tue, 2 Sep 2026 09:00:00", "Follow-up: budget is $50k, launch in June."),
        ]
    }

    result = mail_utils.fetch_thread_context(gmail, "t1")

    assert "Initial ask, thin on detail." in result
    assert "Follow-up: budget is $50k, launch in June." in result
    # Oldest message's text appears before the newer one's.
    assert result.index("Initial ask") < result.index("Follow-up")


def test_fetch_thread_context_single_message_thread():
    gmail = MagicMock()
    gmail.users().threads().get().execute.return_value = {
        "messages": [_fake_gmail_message("client@example.com", "Mon, 1 Sep 2026", "Just one message.")]
    }

    result = mail_utils.fetch_thread_context(gmail, "t1")

    assert "Just one message." in result


def test_walk_for_plain_text_finds_nested_part():
    body = "The actual text, nested under a multipart wrapper."
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/html", "body": {"data": _b64("<p>html version</p>")}},
            {"mimeType": "text/plain", "body": {"data": _b64(body)}},
        ],
    }

    assert mail_utils._walk_for_plain_text(payload) == body
