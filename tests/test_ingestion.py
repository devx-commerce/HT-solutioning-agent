"""Tests for the thread-lock rules and re-open behavior added 2026-09-25.

Scope is deliberately narrow: these rules were the actual source of two
real incidents (every reply after the first was silently ignored forever;
a single failure locked a thread out permanently), so this file exists to
pin the exact state-transition table down, not to be a general-purpose
test suite for the whole ingestion pipeline. See docs/EMAIL-POLLER-DESIGN.md
"Thread locking" for the rules in prose.
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

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
    mock_classify.classify_and_extract.assert_not_called()
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
    mock_classify.classify_and_extract.assert_not_called()
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
    mock_storage.open_thread.assert_called_once_with("t1", "branch_b")
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
    mock_classify.classify_and_extract.return_value = MagicMock(
        is_solution_request=True, reason="genuine request", confidence=0.9,
        client_name="Acme", brief="brief", touchpoints="Digital", category="Retail",
    )

    outcome = ingestion._process_branch_a(MagicMock(), "secret", "m2", force=False, dry_run=True)

    assert outcome == "queued"
    mock_storage.open_thread.assert_called_once_with("t1", "branch_a")


@patch("app.pipeline.ingestion.mail_utils")
@patch("app.pipeline.ingestion.storage")
def test_branch_a_classifies_against_full_thread_not_single_message(mock_storage, mock_mail_utils):
    """The other half of the 2026-09-25 change: classification must use
    the whole thread, since the substance often lands in a later reply."""
    mock_storage.decision_exists.return_value = False
    mock_storage.thread_status.return_value = None
    mock_mail_utils.fetch_message.return_value = _fake_message()
    mock_mail_utils.fetch_thread_context.return_value = "SUBSTANCE: budget $50k, launches in June"

    with patch("app.pipeline.ingestion.classify") as mock_classify, \
         patch("app.pipeline.ingestion.pubsub"):
        mock_classify.classify_and_extract.return_value = MagicMock(
            is_solution_request=False, reason="not a request", confidence=0.5,
            client_name=None, brief=None, touchpoints=None, category=None,
        )
        ingestion._process_branch_a(MagicMock(), "secret", "m1", force=False, dry_run=True)

        called_subject, called_body = mock_classify.classify_and_extract.call_args[0]
        assert called_body == "SUBSTANCE: budget $50k, launches in June"
        assert called_body != _fake_message().body


# --- the sweep must not read the agent's own notifications back ------------
# The deck-built notification goes to SOLUTIONING_NOTIFY_EMAIL, which is the
# same mailbox being swept, so a missing exclusion here is a self-trigger
# loop — one such notification classified as a solution request live.


def test_self_filter_excludes_own_sends_and_notify_address(monkeypatch):
    monkeypatch.setenv("SOLUTIONING_NOTIFY_EMAIL", "sales.agent@hindustantimes.com")
    assert ingestion._self_filter() == (
        "-from:me -from:sales.agent@hindustantimes.com"
    )


def test_self_filter_without_notify_address_still_excludes_own_sends(monkeypatch):
    monkeypatch.delenv("SOLUTIONING_NOTIFY_EMAIL", raising=False)
    assert ingestion._self_filter() == "-from:me"


def test_branch_a_query_carries_the_self_filter(monkeypatch):
    monkeypatch.setenv("SOLUTIONING_NOTIFY_EMAIL", "sales.agent@hindustantimes.com")
    gmail = MagicMock()
    gmail.users().messages().list().execute.return_value = {"messages": []}

    ingestion._list_branch_a(gmail, datetime(2026, 1, 1, tzinfo=timezone.utc))

    query = gmail.users().messages().list.call_args.kwargs["q"]
    assert "-from:me" in query
    assert "-from:sales.agent@hindustantimes.com" in query
