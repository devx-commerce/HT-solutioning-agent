"""Pulling the parts of a Gmail message both /handle_message and ingestion
need — subject, plain-text body, thread id, arrival time. No parsing beyond
that; field extraction is classify.py's job, not this module's.
"""

from __future__ import annotations

import base64
import email.utils
import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree


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


# Where a reply's quote of the earlier conversation begins: everything after
# it repeats messages already in the thread. A forwarded message is kept:
# it is often the brief itself.
_QUOTE_START = re.compile(r"\n\s*On .{5,200}?wrote:\s*\n", re.S)


def own_text(body: str) -> str:
    """A message's own words, without its quote of the earlier conversation."""
    body = _QUOTE_START.split(body, maxsplit=1)[0]
    body = "\n".join(line for line in body.splitlines() if not line.lstrip().startswith(">"))
    return re.sub(r"\n{3,}", "\n\n", body).strip()


def fetch_thread_context(gmail, thread_id: str, owner: str = "", new_message_id: str = "",
                         only_new: bool = False) -> str:
    """The whole thread's content, oldest first, one block per message.

    A thread can carry its real substance in a later reply (budget,
    timeline, a scope change) rather than the message that happened to
    trigger classification — classify against the whole conversation, not
    just whichever single message tripped the filter.

    Each message is its own words only (quotes repeat earlier messages). The
    inbox owner's messages and the new message are marked, so the reader can
    tell a request made to the owner from one the owner made to someone else.
    """
    thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()
    blocks = []
    for msg in thread.get("messages", []):
        headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
        sender = headers.get("From", "")
        tags = []
        if msg.get("id") and msg.get("id") == new_message_id:
            tags.append("NEW EMAIL")
        if owner and email.utils.parseaddr(sender)[1].lower() == owner.lower():
            tags.append("sent by the inbox owner")
        label = f"[{', '.join(tags)}] " if tags else ""
        body = own_text(_walk_for_plain_text(msg["payload"]))
        cc = f"\nCc: {headers['Cc']}" if headers.get("Cc") else ""
        blocks.append(f"{label}From: {sender}\nTo: {headers.get('To', '')}{cc}\n"
                      f"Date: {headers.get('Date', '')}\n\n{body}")
    if new_message_id and any(b.startswith("[NEW EMAIL") for b in blocks):
        # The email being judged first, then the conversation it belongs to.
        new = next(b for b in blocks if b.startswith("[NEW EMAIL"))
        if only_new:
            return new
        rest = [b for b in blocks if b is not new]
        return "\n\n---\n\n".join(
            [new] + (["Earlier in this thread, oldest first:"] + rest if rest else []))
    return "\n\n---\n\n".join(blocks)


# Attachment text, so a brief sent as a document is read. Capped: this goes
# into model prompts.
_ATTACHMENT_MAX_CHARS = 6000
_ATTACHMENTS_MAX_CHARS = 15000


def _xml_text(z: zipfile.ZipFile, names: list[str]) -> str:
    out = []
    for name in names:
        root = ElementTree.fromstring(z.read(name))
        out.append(" ".join(t.text for t in root.iter() if t.tag.endswith("}t") and t.text))
    return "\n".join(out)


def _document_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith((".txt", ".csv")):
        return data.decode("utf-8", "replace")
    if name.endswith(".pdf"):
        from pypdf import PdfReader
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    if name.endswith((".docx", ".pptx", ".xlsx")):
        z = zipfile.ZipFile(io.BytesIO(data))
        files = z.namelist()
        if name.endswith(".docx"):
            return _xml_text(z, [n for n in files if n == "word/document.xml"])
        if name.endswith(".pptx"):
            slides = sorted((n for n in files if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                            key=lambda n: int(re.findall(r"\d+", n)[0]))
            return _xml_text(z, slides)
        # xlsx: cell text lives in the shared strings table.
        return _xml_text(z, [n for n in files if n == "xl/sharedStrings.xml"])
    return ""


def attachment_texts(gmail, thread_id: str, message_id: str = "") -> list[tuple[str, str]]:
    """(file name, text) for each readable attachment in a thread, newest first.

    Word, PowerPoint, Excel, PDF, text and CSV. Anything else, or anything
    that can't be read, is skipped: an attachment never stops a build.
    With message_id, only that email's attachments.
    """
    thread = gmail.users().threads().get(userId="me", id=thread_id, format="full").execute()

    def parts(p):
        yield p
        for c in p.get("parts", []) or []:
            yield from parts(c)

    found, total = [], 0
    for msg in reversed(thread.get("messages", [])):
        if message_id and msg.get("id") != message_id:
            continue
        for part in parts(msg["payload"]):
            filename, att = part.get("filename"), part.get("body", {}).get("attachmentId")
            if not filename or not att or total >= _ATTACHMENTS_MAX_CHARS:
                continue
            try:
                data = base64.urlsafe_b64decode(
                    gmail.users().messages().attachments()
                    .get(userId="me", messageId=msg["id"], id=att).execute()["data"]
                )
                text = re.sub(r"\s+", " ", _document_text(filename, data)).strip()
            except Exception:  # noqa: BLE001 - an unreadable file is skipped
                continue
            if text:
                text = text[:min(_ATTACHMENT_MAX_CHARS, _ATTACHMENTS_MAX_CHARS - total)]
                found.append((filename, text))
                total += len(text)
    return found
