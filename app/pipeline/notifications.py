"""Emails the poller sends as itself — never as, or to, an external party.

Two things live here, both sent from the one shared system identity, never
from an onboarded mailbox: a reauth prompt (the part re-onboarding itself
was missing), and the deck-built notification. Neither is a reply on a
client thread: the notification goes to the HT inbox the brief arrived in,
never out to whoever sent the triggering email. That's what makes `send` (not just `draft`) safe here:
nothing in this module ever addresses an external recipient.
"""

from __future__ import annotations

import base64
import email.mime.multipart
import email.mime.text
import html
import os
import re
import urllib.parse

from googleapiclient.discovery import build
from markdown_it import MarkdownIt

from ..auth.oauth_creds import get_credentials

SERVICE_NAME = "Solutioning Agent"


def send_reauth_prompt(email_address: str) -> None:
    onboarding_url = f"{os.environ.get('SERVICE_URL', '')}/oauth/gmail/start"

    body = (
        f"{SERVICE_NAME} can no longer read your inbox, so your access needs to "
        f"be renewed.\n\n"
        f"Click here to reconnect (takes 10 seconds, same as the first time):\n"
        f"{onboarding_url}\n\n"
        f"Nothing else has changed. This happens only when access to your "
        f"inbox was removed, for example by revoking it in your Google account "
        f"settings or by an IT policy change."
    )
    message = email.mime.text.MIMEText(body)
    message["to"] = email_address
    message["subject"] = f"{SERVICE_NAME}: please reconnect your inbox"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    gmail = build("gmail", "v1", credentials=get_credentials())
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


_SOURCE_LABELS = {
    "past_decks": "HT past decks",
    "web_search": "Web and social",
    "youtube": "YouTube",
    "fetch_url": "Client website",
}


def _sources_section(retrievals: list[dict]) -> str:
    """Which sources returned something and which didn't, from telemetry.

    Stating the ones that came back empty is the point, not an omission —
    a reader has to be able to tell "no prior HT work exists" apart from
    "the past-decks corpus was unreachable".
    """
    if not retrievals:
        return "  (no retrieval was recorded for this brief)"
    outcomes = {
        "success": "returned results",
        "no_results": "returned nothing",
        "error": "could not be reached",
    }
    return "\n".join(
        f"  - {_SOURCE_LABELS.get(r['source'], r['source'])}: "
        f"{outcomes.get(r['outcome'], r['outcome'])}{_search_count(r)}"
        for r in retrievals
    )


def _search_count(r: dict) -> str:
    """" (1 of 4 searches)" when a source was searched more than once."""
    calls = r.get("calls") or 1
    if calls < 2:
        return ""
    if r.get("outcome") == "success":
        return f" ({r.get('successes', 0)} of {calls} searches)"
    return f" ({calls} searches)"


def refinement_link(client_name: str | None, brief_id: str, recipient: str = "") -> str | None:
    """The deep link to the refinement loop: the Solutioning Agent in Gemini
    Enterprise, opened on a new chat with the deck already named in the
    message box ("On the Sleepwell deck (brief 1a0f…), change "), so the
    person only has to finish the sentence. None if no agent URL is set.

    authuser opens it as the person the email goes to, whatever their
    browser's default Google account is. Anyone outside HT's domains can't
    use Gemini Enterprise as themselves, so theirs opens as the agent account.
    """
    base = os.environ.get("GE_AGENT_URL", "").rstrip("/")
    if not base:
        return None
    ht_domains = {d.strip().lower() for d in os.environ.get("ALLOWED_ONBOARD_DOMAIN", "").split(",") if d.strip()}
    account = recipient if recipient.rsplit("@", 1)[-1].lower() in ht_domains else os.environ.get("AGENT_EMAIL", "")
    prompt = f"On the {client_name or 'client'} deck (brief {brief_id}), change "
    user = f"authuser={urllib.parse.quote(account, safe='')}&" if account else ""
    return f"{base}/session/-?{user}q={urllib.parse.quote(prompt, safe='')}"


def send_deck_notification(
    to: str,
    client_name: str | None,
    brief: str | None,
    deck_link: str | None = None,
    evidence: str | None = None,
    gaps: list[str] | None = None,
    retrievals: list[dict] | None = None,
    allowed_urls: set[str] | None = None,
    refine_link: str | None = None,
) -> None:
    """One new email to the inbox the brief came from: never a reply on the
    triggering thread, which may have external participants.

    Carries the four things the SOW asks for in a single message: the
    brief, an evidence summary, the gaps, and the draft.
    """
    if not to:
        raise RuntimeError("No recipient for the deck notification, so there is nowhere to send it.")
    if gaps is None and evidence:
        # The agent lists its gaps inside its reply; lift them into their own
        # section rather than leaving it empty while the evidence repeats them.
        evidence, gaps = split_gaps(evidence)
    if allowed_urls is not None and evidence:
        evidence, removed = drop_unverified_claims(
            evidence, allowed_urls | ({deck_link} if deck_link else set())
        )
        if removed:
            gaps = [*(gaps or []), (
                f"{removed} claim{'s' if removed != 1 else ''} removed because "
                "it cited a link no research tool returned for this brief."
            )]

    if evidence:
        evidence = uniform_links(evidence, deck_link)

    gap_lines = (
        "\n".join(f"  - {g.replace('**', '')}" for g in gaps)
        if gaps
        else "  (none reported)"
    )
    body = f"""A solution deck has been drafted.

CLIENT
  {client_name or '(not extracted)'}

BRIEF
  {brief or '(not extracted)'}

DRAFT DECK
  {deck_link or '(no deck link was returned)'}
{f"""
REFINE THIS DECK
  Opens the Solutioning Agent with this deck named; finish the sentence and send.
  {refine_link}
""" if refine_link else ""}
EVIDENCE SUMMARY
{evidence or '  (none reported)'}

GAPS: not established, do not assume
{gap_lines}

SOURCES CHECKED
{_sources_section(retrievals or [])}

Commercials are not included: no rate, price or commercial term is
generated by the agent. This is a first draft for a person to take
forward, not a client-ready document.
"""
    # The agent writes its evidence in Markdown. As plain text, Gmail shows
    # the ###, ** and [text](url) literally, so send HTML with the plain text
    # as the fallback part for clients that won't render it.
    message = email.mime.multipart.MIMEMultipart("alternative")
    message.attach(email.mime.text.MIMEText(body, "plain", "utf-8"))
    message.attach(email.mime.text.MIMEText(
        _html_body(client_name, brief, deck_link, evidence, gaps, retrievals, refine_link),
        "html", "utf-8",
    ))
    message["to"] = to
    message["subject"] = f"Solution deck drafted: {client_name or 'unspecified client'}"
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

    gmail = build("gmail", "v1", credentials=get_credentials())
    gmail.users().messages().send(userId="me", body={"raw": raw}).execute()


# HT's deck palette, so the email reads as the same product as the deck.
_INK, _MUTED, _CYAN, _BLUE, _RULE = "#1A1A1A", "#5F6368", "#008BA5", "#005CA9", "#DCE6EA"
_HEAD = f"font-family:Georgia,'Times New Roman',serif;color:{_INK};"
# Gmail keeps inline styles reliably and drops much of a <style> block, so
# Markdown's bare tags get their styles written onto them.
_INLINE = {
    "<h1>": f'<h3 style="{_HEAD}font-size:18px;margin:20px 0 8px;">',
    "</h1>": "</h3>",
    "<h2>": f'<h3 style="{_HEAD}font-size:17px;margin:20px 0 8px;">',
    "</h2>": "</h3>",
    "<h3>": f'<h3 style="{_HEAD}font-size:16px;margin:18px 0 6px;">',
    "<h4>": f'<h4 style="{_HEAD}font-size:15px;margin:14px 0 6px;">',
    "<p>": '<p style="margin:0 0 10px;">',
    "<ul>": '<ul style="margin:0 0 10px;padding-left:20px;">',
    "<ol>": '<ol style="margin:0 0 10px;padding-left:20px;">',
    "<li>": '<li style="margin:0 0 6px;">',
    "<a ": f'<a style="color:{_BLUE};" ',
    "<hr>": f'<hr style="border:0;border-top:1px solid {_RULE};margin:18px 0;">',
    "<code>": '<code style="font-family:Menlo,Consolas,monospace;font-size:12px;">',
}


# CommonMark, not Python-Markdown: models nest a bullet under "1. " with 3
# spaces and no blank line, which CommonMark reads as a nested list and
# Python-Markdown collapses into the parent item with its "*" showing.
_MD = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable("table")


# A heading or list item that opens the agent's gaps block: "### Gaps &
# Unknowns", "3. **Gaps & Unestablished Information:**", "What I could not
# establish".
_GAPS_OPENER = re.compile(
    r"^(#{1,6}\s+|\d+[.)]\s+|[*+-]\s+)?\**\s*(\d+[.)]\s*)?[^.]{0,40}?"
    r"\b(gaps?|unknowns?|could not (be )?establish(ed)?|not (yet )?established)\b",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^(\s*)(?:[*+-]|\d+[.)])\s+(.*)$")


def split_gaps(evidence: str) -> tuple[str, list[str]]:
    """Split the agent's gaps block out of its reply.

    Returns the reply without that block, and the block's bullet items. If
    there is no recognisable block, the reply is returned unchanged with no
    gaps -- nothing is guessed.
    """
    lines = evidence.split("\n")
    for start, line in enumerate(lines):
        # Only a short heading-like line opens the block, never a sentence
        # that happens to mention a gap.
        if len(line.strip()) > 90 or not _GAPS_OPENER.match(line.strip()):
            continue
        opener_indent = len(line) - len(line.lstrip())
        is_heading = line.lstrip().startswith("#")
        gaps, end = [], start + 1
        while end < len(lines):
            cur = lines[end]
            text = cur.strip()
            indent = len(cur) - len(cur.lstrip())
            if text.startswith("#") or text in ("---", "***"):
                break
            m = _BULLET.match(cur)
            if m:
                # A sibling of the opener list item ends the block.
                if not is_heading and indent <= opener_indent:
                    break
                gaps.append(m.group(2).strip())
            elif text and gaps and indent > opener_indent:
                gaps[-1] += " " + text  # wrapped continuation line
            elif text and not is_heading and indent <= opener_indent:
                break
            end += 1
        if gaps:
            rest = "\n".join(lines[:start] + lines[end:]).strip()
            return rest, gaps
    return evidence, []


_LINK = re.compile(r"\]\((https?://[^)\s]+)\)|(https?://[^\s)\]>]+)")
_YOUTUBE_ID = re.compile(r"(?:youtube\.com/watch\?(?:.*&)?v=|youtu\.be/)([\w-]{6,})")
_DRIVE_FILE_ID = re.compile(r"(?:[?&]id=|/d/)([-\w]{25,})")


_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_PLACEHOLDER_TEXT = re.compile(r"^(?:ht past deck|sources?|link|here|deck)\s*[:\-]?\s*", re.I)


def uniform_links(evidence: str, deck_link: str | None = None) -> str:
    """Every link in the agent's reply named the same way, whatever it wrote:
    the site for a web page, "HT past deck: <name>" for a past deck, "YouTube"
    for a video, "Draft deck" for this deck."""
    deck_key = _url_key(deck_link) if deck_link else None

    def name(m: re.Match) -> str:
        text, url = m.group(1).strip(), m.group(2)
        if deck_key and _url_key(url) == deck_key:
            label = "Draft deck"
        elif "drive.google.com" in url or "docs.google.com" in url:
            deck = re.sub(r"\.(pptx|pdf)$", "", _PLACEHOLDER_TEXT.sub("", text), flags=re.I).strip()
            label = f"HT past deck: {deck}" if deck else "HT past deck"
        elif _YOUTUBE_ID.search(url):
            label = "YouTube"
        else:
            label = urllib.parse.urlsplit(url).netloc.lower().removeprefix("www.")
        return f"[{label}]({url})"

    return _MD_LINK.sub(name, evidence)


def _url_key(url: str) -> str:
    """One key per destination, so a cited link matches what a tool returned
    despite trailing slashes, tracking parameters or a Drive link's format."""
    url = url.strip().rstrip(".,;:")
    if "drive.google.com" in url or "docs.google.com" in url:
        m = _DRIVE_FILE_ID.search(url)
        if m:
            return f"drive:{m.group(1)}"
    m = _YOUTUBE_ID.search(url)
    if m:
        return f"youtube:{m.group(1)}"
    parts = urllib.parse.urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    query = urllib.parse.urlencode([
        (k, v) for k, v in urllib.parse.parse_qsl(parts.query)
        if not k.lower().startswith("utm_")
    ])
    return f"{host}{parts.path.rstrip('/')}" + (f"?{query}" if query else "")


def drop_unverified_claims(evidence: str, allowed_urls: set[str]) -> tuple[str, int]:
    """Remove every line that cites a link no research tool returned.

    The agent's reply is free text, so its citations are only as good as its
    copying: a link it rewrote, shortened or supplied from memory would
    otherwise reach the email looking like evidence. Fails closed: a line
    with any unverified link goes, even if it also cites a good one.
    """
    allowed = {_url_key(u) for u in allowed_urls}
    kept, removed = [], 0
    for line in evidence.split("\n"):
        links = [a or b for a, b in _LINK.findall(line)]
        if any(_url_key(u) not in allowed for u in links):
            removed += 1
            continue
        kept.append(line)
    return "\n".join(kept), removed


def _inline_html(text: str) -> str:
    out = _MD.renderInline(text or "")
    return out.replace("<a ", _INLINE["<a "])


def _markdown_html(text: str) -> str:
    out = _MD.render(text or "")
    for tag, styled in _INLINE.items():
        out = out.replace(tag, styled)
    return out


def _section(title: str, inner: str) -> str:
    return (
        f'<tr><td style="padding:18px 0 0;">'
        f'<div style="font-size:11px;font-weight:bold;letter-spacing:1px;'
        f'text-transform:uppercase;color:{_BLUE};margin:0 0 8px;">{html.escape(title)}</div>'
        f"{inner}</td></tr>"
    )


def _html_body(client_name, brief, deck_link, evidence, gaps, retrievals, refine_link=None) -> str:
    client = html.escape(client_name or "(not extracted)")
    deck = (
        f'<a href="{html.escape(deck_link)}" style="display:inline-block;background:{_CYAN};'
        f'color:#FFFFFF;text-decoration:none;font-weight:bold;padding:10px 18px;'
        f'border-radius:6px;">Open the draft deck in Google Slides</a>'
        if deck_link else f'<p style="color:{_MUTED};">No deck link was returned.</p>'
    )
    if refine_link:
        deck += (
            f'&nbsp;&nbsp;<a href="{html.escape(refine_link)}" style="display:inline-block;'
            f'border:1px solid {_CYAN};color:{_CYAN};text-decoration:none;font-weight:bold;'
            f'padding:9px 17px;border-radius:6px;">Refine this deck with the agent</a>'
            f'<p style="margin:10px 0 0;color:{_MUTED};font-size:12px;">Opens the Solutioning Agent in '
            f'Gemini Enterprise with this deck already named. Finish the sentence, for example '
            f'"the picture on slide 6 to a pandal at night", and send.</p>'
        )
    gap_html = (
        "<ul style=\"margin:0;padding-left:20px;\">"
        + "".join(f'<li style="margin:0 0 6px;">{_inline_html(g)}</li>' for g in gaps)
        + "</ul>"
        if gaps else f'<p style="margin:0;color:{_MUTED};">None reported.</p>'
    )
    outcomes = {"success": "returned results", "no_results": "returned nothing",
                "error": "could not be reached"}
    sources = (
        "<ul style=\"margin:0;padding-left:20px;\">" + "".join(
            f'<li style="margin:0 0 4px;"><b>{html.escape(_SOURCE_LABELS.get(r["source"], r["source"]))}</b>: '
            f'{html.escape(outcomes.get(r["outcome"], r["outcome"]) + _search_count(r))}</li>'
            for r in retrievals
        ) + "</ul>"
        if retrievals else f'<p style="margin:0;color:{_MUTED};">No retrieval was recorded for this brief.</p>'
    )
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#FFFFFF;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#FFFFFF;">
<tr><td align="center" style="padding:24px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
  style="max-width:680px;font-family:Arial,Helvetica,sans-serif;font-size:14px;line-height:1.55;color:{_INK};">
<tr><td>
  <div style="width:44px;height:4px;background:{_CYAN};border-radius:2px;margin:0 0 14px;"></div>
  <div style="font-size:11px;font-weight:bold;letter-spacing:1px;text-transform:uppercase;color:{_BLUE};">Solution deck drafted</div>
  <h1 style="{_HEAD}font-size:24px;margin:6px 0 0;">{client}</h1>
</td></tr>
{_section("Brief", f'<p style="margin:0;">{html.escape(brief or "(not extracted)")}</p>')}
{_section("Draft deck", deck)}
{_section("Evidence and findings", _markdown_html(evidence) if evidence else f'<p style="color:{_MUTED};">None reported.</p>')}
{_section("Gaps: not established, do not assume", gap_html)}
{_section("Sources checked", sources)}
<tr><td style="padding:22px 0 0;border-top:1px solid {_RULE};color:{_MUTED};font-size:12px;">
  Commercials are not included: the agent generates no rate, price or commercial term.
  This is a first draft for a person to take forward, not a client-ready document.
</td></tr>
</table></td></tr></table></body></html>"""
