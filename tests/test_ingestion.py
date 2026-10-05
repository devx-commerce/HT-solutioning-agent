"""Tests for the thread-lock rules and re-open behavior added 2026-09-25.

Scope is deliberately narrow: these rules were the actual source of two
real incidents (every reply after the first was silently ignored forever;
a single failure locked a thread out permanently), so this file exists to
pin the exact state-transition table down, not to be a general-purpose
test suite for the whole ingestion pipeline. See developer-docs/EMAIL-POLLER-DESIGN.md
"Thread locking" for the rules in prose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, ANY

import pytest

from app.pipeline import ingestion, mail_utils


# --- _thread_lock_reason: pure function, every state x branch combination ---


@pytest.mark.parametrize("triggered_by", ["branch_a", "branch_b"])
def test_no_existing_thread_always_proceeds(triggered_by):
    assert ingestion._thread_lock_reason(None, triggered_by) is None


@pytest.mark.parametrize("triggered_by", ["branch_a", "branch_b"])
def test_building_always_blocks(triggered_by):
    existing = {"status": "building", "sheet_row_written": False, "brief_id": None}
    assert ingestion._thread_lock_reason(existing, triggered_by) is not None


def test_built_blocks_automatic_detection():
    existing = {"status": "built", "sheet_row_written": True, "brief_id": "x"}
    assert ingestion._thread_lock_reason(existing, "branch_a") is not None


def test_built_allows_manual_override():
    existing = {"status": "built", "sheet_row_written": True, "brief_id": "x"}
    assert ingestion._thread_lock_reason(existing, "branch_b") is None


@pytest.mark.parametrize("triggered_by", ["branch_a", "branch_b"])
def test_failed_always_allows_retry(triggered_by):
    existing = {"status": "failed", "sheet_row_written": False, "brief_id": None}
    assert ingestion._thread_lock_reason(existing, triggered_by) is None


# --- _process_branch_a / _process_branch_b against a live thread_status ---
# Mocks every external call (Gmail, BigQuery, the classifier, Pub/Sub) —
# these test control flow only, not the real APIs.


def _fake_message(thread_id="t1", message_id="m1"):
    return mail_utils.ParsedMessage(
        message_id=message_id,
        thread_id=thread_id,
        subject="Ad partnership inquiry",
        body="We'd like to discuss a partnership.",
        received_at=datetime(2026, 9, 25, tzinfo=timezone.utc),
        sender="client@example.com",
        rfc_message_id="<abc@example.com>",
    )


@patch("app.pipeline.ingestion.pubsub")
@patch("app.pipeline.ingestion.classify")
@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_branch_a_skips_when_thread_already_building(mock_storage, mock_mail_utils, mock_classify, mock_pubsub):
    mock_storage.decision_exists.return_value = False
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_storage.thread_status.return_value = {"status": "building", "sheet_row_written": False, "brief_id": None}

    outcome = ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True)

    assert outcome == "skipped"
    mock_classify.decide.assert_not_called()
    mock_storage.open_thread.assert_not_called()
    mock_pubsub.publish_build_task.assert_not_called()


@patch("app.pipeline.ingestion.pubsub")
@patch("app.pipeline.ingestion.classify")
@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_branch_a_skips_when_thread_already_built(mock_storage, mock_mail_utils, mock_classify, mock_pubsub):
    mock_storage.decision_exists.return_value = False
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_storage.thread_status.return_value = {"status": "built", "sheet_row_written": True, "brief_id": "x"}

    outcome = ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True)

    assert outcome == "skipped"
    mock_classify.decide.assert_not_called()
    mock_pubsub.publish_build_task.assert_not_called()


@patch("app.pipeline.ingestion.pubsub")
@patch("app.pipeline.ingestion.classify")
@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_branch_b_allowed_when_thread_already_built(mock_storage, mock_mail_utils, mock_classify, mock_pubsub):
    """The actual behavior change requested 2026-09-25: a human explicitly
    re-labeling an already-built thread must still trigger a fresh build."""
    mock_storage.decision_exists.return_value = False
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_mail_utils.fetch_thread_context.return_value = "full thread text"
    mock_storage.thread_status.return_value = {"status": "built", "sheet_row_written": True, "brief_id": "x"}
    mock_classify.extract_only.return_value = MagicMock(
        has_content=True, client_name="Acme", brief="brief", touchpoints="Digital", category="Retail",
    )

    outcome = ingestion._process_branch_b(MagicMock(), "secret", "m1", force=False, dry_run=True)

    assert outcome == "queued"
    mock_storage.open_thread.assert_called_once_with("t1", "branch_b", received_at=ANY)
    mock_pubsub.publish_build_task.assert_called_once()


@patch("app.pipeline.ingestion.pubsub")
@patch("app.pipeline.ingestion.classify")
@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_branch_a_allowed_when_thread_failed(mock_storage, mock_mail_utils, mock_classify, mock_pubsub):
    """The other behavior change requested 2026-09-25: a genuine new
    message in a previously-failed thread must get a real new attempt."""
    mock_storage.decision_exists.return_value = False
    mock_mail_utils.fetch_message.return_value = _fake_message(message_id="m2")
    mock_mail_utils.fetch_thread_context.return_value = "full thread text"
    mock_storage.thread_status.return_value = {"status": "failed", "sheet_row_written": False, "brief_id": None}
    mock_classify.decide.return_value = MagicMock(
        is_solution_request=True, reason="genuine request", confidence=0.9,
        client_name="Acme", brief="brief", touchpoints="Digital", category="Retail",
    )

    outcome = ingestion._process_branch_a(MagicMock(), "secret", "m2", force=False, dry_run=True)

    assert outcome == "queued"
    mock_storage.open_thread.assert_called_once_with("t1", "branch_a", received_at=ANY)


@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_branch_a_decides_on_the_new_email_and_writes_the_brief_from_the_thread(mock_storage, mock_mail_utils):
    """Decided on the new email alone (a long thread gets an old ask pinned on
    a short reply); the whole thread is read only to write the brief."""
    mock_storage.decision_exists.return_value = False
    mock_storage.thread_status.return_value = None
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_mail_utils.attachment_texts.return_value = []
    mock_mail_utils.fetch_thread_context.side_effect = (
        lambda gmail, tid, owner="", new_message_id="", only_new=False: "NEW ONLY" if only_new else "WHOLE THREAD")

    with patch("app.pipeline.ingestion.classify") as mock_classify, \
         patch("app.pipeline.ingestion.pubsub"):
        mock_classify.decide.return_value = MagicMock(is_solution_request=True, reason="r", confidence=0.9)
        mock_classify.extract_only.return_value = MagicMock(client_name="Acme", brief="b", touchpoints=None, category=None)
        ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True, mailbox="o@htdigital.in")

        assert mock_classify.decide.call_args[0][1].endswith("NEW ONLY")
        assert mock_classify.extract_only.call_args[0][1].endswith("WHOLE THREAD")


@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_a_reply_that_is_not_a_brief_never_reads_the_whole_thread(mock_storage, mock_mail_utils):
    mock_storage.decision_exists.return_value = False
    mock_storage.thread_status.return_value = None
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_mail_utils.attachment_texts.return_value = []
    mock_mail_utils.fetch_thread_context.return_value = "6 inserts"
    with patch("app.pipeline.ingestion.classify") as mock_classify, \
         patch("app.pipeline.ingestion.pubsub") as mock_pubsub:
        mock_classify.decide.return_value = MagicMock(is_solution_request=False, reason="rate query", confidence=0.9)
        assert ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True) == "rejected"
        mock_classify.extract_only.assert_not_called()
        mock_pubsub.publish_build_task.assert_not_called()


# --- the sweep must not read the agent's own notifications back ------------
# The deck-built notification is sent from AGENT_EMAIL into the inbox being
# swept, so a missing exclusion here is a self-trigger loop; one such
# notification was classified as a solution request live.


def test_self_filter_excludes_own_sends_and_notify_address(monkeypatch):
    monkeypatch.setenv("AGENT_EMAIL", "sales.agent@hindustantimes.com")
    assert ingestion._self_filter() == (
        "-from:me -from:sales.agent@hindustantimes.com"
    )


def test_self_filter_without_notify_address_still_excludes_own_sends(monkeypatch):
    monkeypatch.delenv("AGENT_EMAIL", raising=False)
    assert ingestion._self_filter() == "-from:me"


def test_branch_a_query_carries_the_self_filter(monkeypatch):
    monkeypatch.setenv("AGENT_EMAIL", "sales.agent@hindustantimes.com")
    gmail = MagicMock()
    gmail.users().messages().list().execute.return_value = {"messages": []}

    ingestion._list_branch_a(gmail, datetime(2026, 1, 1, tzinfo=timezone.utc))

    query = gmail.users().messages().list.call_args.kwargs["q"]
    assert "-from:me" in query
    assert "-from:sales.agent@hindustantimes.com" in query


def test_branch_a_query_excludes_every_listed_internal_sender():
    gmail = MagicMock()
    gmail.users().messages().list().execute.return_value = {"messages": []}

    ingestion._list_branch_a(gmail, datetime(2026, 1, 1, tzinfo=timezone.utc))

    query = gmail.users().messages().list.call_args.kwargs["q"]
    for addr in (
        "hrtimes@hindustantimes.com",
        "ithelpdesk@hindustantimes.com",
        "noreply@darwinbox.in",
        "digests@darwinbox.in",
        "payroll@hindustantimes.com",
        "trending@hindustantimes.com",
        "itcommunication@hindustantimes.com",
    ):
        assert f"-from:{addr}" in query.split()
    # The watermark bound is still there after the added terms.
    assert "after:" in query


def test_a_manually_labelled_email_is_never_sender_filtered():
    """Branch B is a person's explicit request; it overrides the exclusion list."""
    gmail = MagicMock()
    gmail.users().messages().list().execute.return_value = {"messages": []}

    ingestion._list_branch_b(gmail)

    query = gmail.users().messages().list.call_args.kwargs["q"]
    assert "-from:" not in query


# --- the "deck drafted" email goes to the inbox the brief came from --------


@patch("app.pipeline.ingestion.pubsub")
@patch("app.pipeline.ingestion.classify")
@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_a_queued_build_carries_the_inbox_it_came_from(mock_storage, mock_mail_utils, mock_classify, mock_pubsub):
    mock_storage.decision_exists.return_value = False
    mock_storage.thread_status.return_value = None
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_classify.decide.return_value = MagicMock(
        is_solution_request=True, client_name="Acme", brief="b", touchpoints="t", category="c", confidence=0.9,
    )
    ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True,
                                mailbox="am@hindustantimes.com")
    assert mock_pubsub.publish_build_task.call_args.kwargs["mailbox"] == "am@hindustantimes.com"


def _payload(**extra):
    return {"thread_id": "t1", "gmail_secret": "s", "message_id": "m1", "subject": "x",
            "thread_context": "ctx", "received_at": "2026-10-05T10:00:00+00:00",
            "client_name": "Acme", "brief": "b", "touchpoints": "t", "category": "c", **extra}


@pytest.mark.parametrize("payload, expected", [
    (_payload(mailbox="am@htdigital.in"), "am@htdigital.in"),
    (_payload(), "sales.agent@hindustantimes.com"),  # queued before the field existed
])
def test_the_notification_goes_to_the_source_inbox(monkeypatch, payload, expected):
    monkeypatch.setenv("AGENT_EMAIL", "sales.agent@hindustantimes.com")
    with patch("app.pipeline.ingestion.storage") as storage, \
         patch("app.pipeline.ingestion.agent_client") as agent, \
         patch("app.pipeline.ingestion.labels"), \
         patch("app.pipeline.ingestion.sheet"), \
         patch("app.pipeline.ingestion.notifications") as notifications, \
         patch("app.auth.gmail_client.get_service_for_user"):
        storage.thread_status.return_value = None
        agent.invoke_agent.return_value = "Deck built."
        storage.built_deck_link.return_value = "https://docs.google.com/presentation/d/D1/edit"
        ingestion.execute_build(payload)
    assert notifications.send_deck_notification.call_args.args[0] == expected


def _build(payload, deck_link, reply="Here is the deck: https://docs.google.com/presentation/d/OLD/edit"):
    with patch("app.pipeline.ingestion.storage") as storage, \
         patch("app.pipeline.ingestion.agent_client") as agent, \
         patch("app.pipeline.ingestion.labels") as labels, \
         patch("app.pipeline.ingestion.sheet") as sheet, \
         patch("app.pipeline.ingestion.notifications") as notifications, \
         patch("app.auth.gmail_client.get_service_for_user"):
        storage.thread_status.return_value = None
        storage.built_deck_link.return_value = deck_link
        agent.invoke_agent.return_value = reply
        outcome = ingestion.execute_build(payload)
    return outcome, labels, sheet, notifications, storage


def test_an_email_is_labelled_only_when_a_deck_was_saved_for_its_brief():
    outcome, labels, sheet, notifications, storage = _build(_payload(), deck_link=None)
    assert outcome == "failed"
    labels.apply_label.assert_not_called()
    sheet.append_row.assert_not_called()
    notifications.send_deck_notification.assert_not_called()
    storage.mark_thread_failed.assert_called_once()


def test_the_deck_drafted_email_links_the_saved_deck_not_one_the_reply_mentions():
    saved = "https://docs.google.com/presentation/d/NEW/edit"
    outcome, labels, _, notifications, _ = _build(_payload(), deck_link=saved)
    assert outcome == "built"
    labels.apply_label.assert_called_once()
    assert notifications.send_deck_notification.call_args.kwargs["deck_link"] == saved


def test_a_dry_run_never_labels_emails_writes_the_sheet_or_sends_email():
    outcome, labels, sheet, notifications, storage = _build(_payload(dry_run=True), deck_link=None)
    assert outcome == "dry_run"
    labels.apply_label.assert_not_called()
    sheet.append_row.assert_not_called()
    notifications.send_deck_notification.assert_not_called()


@pytest.mark.parametrize("subject", [
    "Invitation: Meeting with Neha regarding Pronamel Kids and Sensodyne @ Mon Oct 5, 2026 3:30pm",
    "Updated invitation: Emami x HT Media @ Tue Oct 6, 2026",
    "Accepted: Senco review @ Wed Oct 7, 2026",
    "Invitation with note: Liberty brief walkthrough",
])
@patch("app.pipeline.ingestion.pubsub")
@patch("app.pipeline.ingestion.classify")
@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_a_calendar_invitation_is_never_a_brief(mock_storage, mock_mail_utils, mock_classify, mock_pubsub, subject):
    mock_storage.decision_exists.return_value = False
    mock_storage.thread_status.return_value = None
    msg = _fake_message(); msg.subject = subject
    mock_mail_utils.fetch_message.return_value = msg

    assert ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True) == "rejected"
    mock_classify.decide.assert_not_called()
    mock_pubsub.publish_build_task.assert_not_called()
    mock_storage.record_decision.assert_called_once_with("m1", "not_a_request", "calendar invitation", confidence=1.0)


@pytest.mark.parametrize("subject", ["Re: Invitation to pitch for Liberty", "Brief: Decathlon campus invitation event"])
def test_a_brief_that_mentions_an_invitation_is_still_classified(subject):
    assert not ingestion._CALENDAR_SUBJECT.match(subject)
