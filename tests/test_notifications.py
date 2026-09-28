"""The deck-drafted email: its Markdown evidence must arrive rendered.

The agent writes Markdown. Sent as plain text, Gmail showed the ###, ** and
[text](url) literally, which is what the solutioning team actually read.
"""

from __future__ import annotations

import base64
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
    monkeypatch.setenv("SOLUTIONING_NOTIFY_EMAIL", "solutioning@example.com")
    captured = {}
    gmail = MagicMock()
    gmail.users.return_value.messages.return_value.send.side_effect = (
        lambda userId, body: captured.update(body) or MagicMock()
    )
    with patch.object(notifications, "build", return_value=gmail), \
         patch.object(notifications, "get_credentials", return_value=None):
        notifications.send_deck_notification(
            "Tata Sampann", "Launch ₹10 sachets in UP and Bihar.",
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
