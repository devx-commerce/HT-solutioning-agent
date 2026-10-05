"""The deck-drafted email: its Markdown evidence must arrive rendered.

The agent writes Markdown. Sent as plain text, Gmail showed the ###, ** and
[text](url) literally, which is what the solutioning team actually read.
"""

from __future__ import annotations

import base64
import html
import urllib.parse
import email
from unittest.mock import MagicMock, patch

import pytest

from app.pipeline import notifications

EVIDENCE = """### Research findings

1. **Brand strategy:** natural oils intact ([MxM India](https://www.mxmindia.com/x)).
2. **Prior HT work:** Maggi Masala ae Magic X HT Media.pptx
"""


@pytest.fixture
def sent(monkeypatch):
    captured = {}
    gmail = MagicMock()
    gmail.users.return_value.messages.return_value.send.side_effect = (
        lambda userId, body: captured.update(body) or MagicMock()
    )
    with patch.object(notifications, "build", return_value=gmail), \
         patch.object(notifications, "get_credentials", return_value=None):
        notifications.send_deck_notification(
            "am@hindustantimes.com", "Tata Sampann", "Launch ₹10 sachets in UP and Bihar.",
            deck_link="https://docs.google.com/presentation/d/abc/edit",
            evidence=EVIDENCE,
            gaps=["District priorities"],
            retrievals=[{"source": "past_decks", "outcome": "success", "calls": 4, "successes": 1},
                        {"source": "youtube", "outcome": "no_results"}],
        )
    return email.message_from_bytes(base64.urlsafe_b64decode(captured["raw"]))


def _part(msg, subtype):
    return next(p for p in msg.walk() if p.get_content_type() == f"text/{subtype}").get_payload(decode=True).decode()


def test_email_has_an_html_part_and_a_plain_text_fallback(sent):
    assert sent.get_content_type() == "multipart/alternative"
    assert "Tata Sampann" in _part(sent, "plain")
    assert "<html>" in _part(sent, "html")


def test_markdown_evidence_is_rendered_not_shown_literally(sent):
    body = _part(sent, "html")
    assert "###" not in body and "**" not in body and "](" not in body
    assert "Research findings</h3>" in body
    assert "<strong>Brand strategy:</strong>" in body
    assert 'href="https://www.mxmindia.com/x"' in body and ">MxM India</a>" in body


def test_bullets_nested_the_way_the_agent_writes_them_render_as_a_nested_list():
    # 3-space indent under "1.", no blank line: exactly the agent's output,
    # which Python-Markdown collapsed into the parent line with "*" showing.
    html = notifications._markdown_html(
        "1. **Brand strategy:**\n   * **Purity:** natural oils intact.\n   * **LUP:** ₹10 sachets.\n"
        "2. **Prior HT work:**\n   * No prior deck found.\n"
    )
    assert html.count("<ul") == 2
    assert "* " not in html
    assert "<strong>Purity:</strong> natural oils intact." in html


def test_deck_link_is_a_button_and_sources_are_listed(sent):
    body = _part(sent, "html")
    assert 'href="https://docs.google.com/presentation/d/abc/edit"' in body
    assert "Open the draft deck in Google Slides" in body
    # one line per source, however many times it was searched
    assert body.count("<b>HT past decks</b>") == 1
    assert "<b>HT past decks</b>: returned results (1 of 4 searches)" in body
    assert "<b>YouTube</b>: returned nothing" in body


def test_subject_and_headings_have_no_em_dashes(sent):
    assert sent["subject"] == "Solution deck drafted: Tata Sampann"
    assert "—" not in _part(sent, "html").replace(EVIDENCE, "")
    assert "—" not in _part(sent, "plain")


# --- the agent's gaps block becomes the Gaps section --------------------------


@pytest.mark.parametrize("reply", [
    # the three shapes the agent actually wrote on 28 Sep
    "1. **Brand:** x\n2. **Prior HT work:** none\n3. **Gaps & Unknowns:**\n"
    "   * **District priorities:** to confirm.\n   * **Commercials:** from pricing.\n\n### Deck\n* link",
    "### Findings\n* one\n\n#### 2. Information Gaps\n* District priorities to confirm.\n"
    "* Commercials from pricing.\n\n---\n\n### Deck\n* link",
    "1. **Brand:** x\n3. **Information Gaps & Open Items**\n   * District priorities to confirm.\n"
    "   * Commercials from pricing.\n4. **Deck:** link",
])
def test_the_agents_gaps_block_is_lifted_out_of_the_evidence(reply):
    rest, gaps = notifications.split_gaps(reply)
    assert len(gaps) == 2
    assert "District priorities" in gaps[0] and "pricing" in gaps[1]
    assert "Gaps" not in rest and "District priorities" not in rest
    assert "Deck" in rest  # what came after the block survives


def test_a_reply_with_no_gaps_block_is_left_unchanged():
    reply = "### Findings\n* one\n* two"
    assert notifications.split_gaps(reply) == (reply, [])


def test_a_sentence_that_mentions_a_gap_is_not_a_gaps_block():
    reply = "### Findings\n* The brand has a clear gap in rural reach that HT's Hindi print can fill, which matters.\n"
    assert notifications.split_gaps(reply) == (reply, [])


# --- every link in the evidence must be one a research tool returned -------


DRIVE_ID = "1DLFGa1d3gfNG5cUDJqgmZjnIfyW7OiaQ"


def test_lines_citing_only_returned_links_are_kept():
    text = "- Maggi ran nukkad nataks ([deck](https://drive.google.com/a/ht.com/open?id=%s))" % DRIVE_ID
    out, removed = notifications.drop_unverified_claims(
        text, {f"https://drive.google.com/open?id={DRIVE_ID}"}
    )
    assert (out, removed) == (text, 0)


def test_a_claim_citing_a_link_no_tool_returned_is_removed():
    text = "\n".join([
        "### Findings",
        "- Real ([afaqs](https://www.afaqs.com/news/a/?utm_source=x)).",
        "- Invented ([ET](https://economictimes.com/made-up)).",
        "- Mixed ([afaqs](https://afaqs.com/news/a)) and https://nowhere.com/b.",
        "No link, kept.",
    ])
    out, removed = notifications.drop_unverified_claims(text, {"https://afaqs.com/news/a"})
    assert removed == 2
    assert out.split("\n") == ["### Findings", "- Real ([afaqs](https://www.afaqs.com/news/a/?utm_source=x)).", "No link, kept."]


@pytest.mark.parametrize("cited, returned", [
    ("https://youtu.be/DozotvPZgCM", "https://www.youtube.com/watch?v=DozotvPZgCM"),
    (f"https://drive.google.com/file/d/{DRIVE_ID}/view", f"https://drive.google.com/open?id={DRIVE_ID}"),
    ("https://www.acme.com/a/", "https://acme.com/a"),
])
def test_the_same_destination_matches_across_link_formats(cited, returned):
    assert notifications._url_key(cited) == notifications._url_key(returned)


def test_removed_claims_are_reported_as_a_gap_and_the_deck_link_survives(monkeypatch):
    captured = {}
    gmail = MagicMock()
    gmail.users.return_value.messages.return_value.send.side_effect = (
        lambda userId, body: captured.update(body) or MagicMock()
    )
    deck = "https://docs.google.com/presentation/d/1XkNNNjxn3rqiusMVmxO3IyeknSSaMquwfrlWWPz5tRA/edit"
    with patch.object(notifications, "build", return_value=gmail), \
         patch.object(notifications, "get_credentials", return_value=None):
        notifications.send_deck_notification(
            "am@hindustantimes.com", "Rapido", "Metro se ghar tak.", deck_link=deck,
            evidence=f"Deck: {deck}\n- Invented ([x](https://made.up/a)).",
            gaps=[], allowed_urls=set(),
        )
    text = _part(email.message_from_bytes(base64.urlsafe_b64decode(captured["raw"])), "plain")
    assert "made.up" not in text
    assert f"Deck: {deck}" in text
    assert "1 claim removed because it cited a link no research tool returned" in text



def test_the_notification_goes_to_the_inbox_the_brief_came_from(sent):
    assert sent["to"] == "am@hindustantimes.com"


def test_no_recipient_is_an_error_not_a_silent_drop():
    with pytest.raises(RuntimeError, match="No recipient"):
        notifications.send_deck_notification("", "Acme", "brief")


AGENT = "https://vertexaisearch.cloud.google.com/us/home/cid/c1/r/agent/15297440114283783461"


@pytest.fixture
def ge(monkeypatch):
    monkeypatch.setenv("GE_AGENT_URL", AGENT)
    monkeypatch.setenv("ALLOWED_ONBOARD_DOMAIN", "hindustantimes.com,htdigital.in")
    monkeypatch.setenv("AGENT_EMAIL", "sales.agent@hindustantimes.com")


def _query(link):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(link).query))


def test_the_refinement_deep_link_opens_the_agent_with_the_deck_named(ge):
    link = notifications.refinement_link("Rocksport", "1a10bf78168a2229", "ankita.suden@htdigital.in")
    assert link.startswith(AGENT + "/session/-?")
    assert _query(link) == {"authuser": "ankita.suden@htdigital.in",
                            "q": "On the Rocksport deck (brief 1a10bf78168a2229), change "}


def test_someone_outside_ht_gets_a_link_that_opens_as_the_agent_account(ge):
    link = notifications.refinement_link("Rocksport", "b1", "navya.agarwal@devxlabs.ai")
    assert _query(link)["authuser"] == "sales.agent@hindustantimes.com"


def test_no_agent_url_means_no_refinement_link(monkeypatch):
    monkeypatch.delenv("GE_AGENT_URL", raising=False)
    assert notifications.refinement_link("Rocksport", "b1") is None


def test_the_email_carries_the_refine_button_when_there_is_a_link():
    captured = {}
    gmail = MagicMock()
    gmail.users.return_value.messages.return_value.send.side_effect = (
        lambda userId, body: captured.update(body) or MagicMock())
    link = AGENT + "/session/-?q=On%20the%20Acme%20deck"
    with patch.object(notifications, "build", return_value=gmail), \
         patch.object(notifications, "get_credentials", return_value=None):
        notifications.send_deck_notification("am@hindustantimes.com", "Acme", "b",
                                             deck_link="https://docs.google.com/presentation/d/abc/edit",
                                             refine_link=link)
    msg = email.message_from_bytes(base64.urlsafe_b64decode(captured["raw"]))
    assert "Refine this deck with the agent" in _part(msg, "html") and html.escape(link) in _part(msg, "html")
    assert link in _part(msg, "plain")
