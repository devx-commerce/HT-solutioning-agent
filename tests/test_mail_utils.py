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


def test_the_owners_emails_and_the_new_email_are_marked_and_quotes_dropped():
    gmail = MagicMock()
    first = _fake_gmail_message("Ankita <ankita@htdigital.in>", "Mon", "Please share mocks for YAS.")
    first["id"] = "m1"
    reply = _fake_gmail_message("Naresh <naresh@hindustantimes.com>", "Tue",
                                "PFA the mocks.\n\nOn Mon, Ankita <ankita@htdigital.in> wrote:\n> Please share mocks for YAS.")
    reply["id"] = "m2"
    gmail.users().threads().get().execute.return_value = {"messages": [first, reply]}
    text = mail_utils.fetch_thread_context(gmail, "t1", owner="ankita@htdigital.in", new_message_id="m2")
    assert "[sent by the inbox owner] From: Ankita" in text
    assert "[NEW EMAIL] From: Naresh" in text
    assert text.count("Please share mocks for YAS.") == 1  # the quote is not repeated


def _zip(files: dict) -> bytes:
    import io, zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'


def test_word_and_excel_attachments_are_read():
    docx = _zip({"word/document.xml": f'<w:document {W}><w:body><w:p><w:r><w:t>Print, digital and video ideas for December</w:t></w:r></w:p></w:body></w:document>'})
    xlsx = _zip({"xl/sharedStrings.xml": f'<sst {S}><si><t>HT City travel feature</t></si><si><t>Digital</t></si></sst>'})
    assert "Print, digital and video ideas" in mail_utils._document_text("Brief.docx", docx)
    assert "HT City travel feature Digital" in mail_utils._document_text("Tracker.xlsx", xlsx)


def test_a_pdf_attachment_is_read():
    import io
    from pypdf import PdfWriter
    buf = io.BytesIO(); w = PdfWriter(); w.add_blank_page(width=200, height=200); w.write(buf)
    assert mail_utils._document_text("brief.pdf", buf.getvalue()) == ""  # a blank page reads as empty, without error


def test_attachments_are_capped_and_unreadable_files_skipped(monkeypatch):
    monkeypatch.setattr(mail_utils, "_ATTACHMENT_MAX_CHARS", 10)
    gmail = MagicMock()
    gmail.users().threads().get().execute.return_value = {"messages": [{"id": "m1", "payload": {"parts": [
        {"filename": "brief.txt", "body": {"attachmentId": "a1"}},
        {"filename": "photo.jpg", "body": {"attachmentId": "a2"}},
    ]}}]}
    gmail.users().messages().attachments().get().execute.return_value = {"data": _b64("A long brief that goes on")}
    found = mail_utils.attachment_texts(gmail, "t1")
    assert found == [("brief.txt", "A long bri")]
