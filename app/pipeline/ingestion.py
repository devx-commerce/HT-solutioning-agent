"""The sweep orchestration, split into two halves at the one expensive step.

`run_sweep_for_user` — called synchronously from /sweep — does the cheap
work: listing both branches, message/thread-level idempotency, and one
cheap-model classification call per candidate. Anything that decides "yes,
build a deck" gets *enqueued* (pubsub.publish_build_task), not built
inline.

`execute_build` — called from /work, the Pub/Sub push target — does the
one expensive step, the Agent Engine invocation, plus everything after it
(label, notify, sheet row). Cloud Run's own concurrency/instance limits on
the service bound how many of these run at once; see pubsub.py for why
that split exists now rather than later.

See docs/EMAIL-POLLER-DESIGN.md for the reasoning behind every decision
here — this module is the implementation of that doc, not a second copy.

Two testing knobs on the classify-and-enqueue half, both off by default:

  force    — ignore the watermark (use the bootstrap cutoff instead) and
             ignore decision_exists/thread_status, so every Branch A/B
             candidate gets reprocessed as if seen for the first time.
             Never advances the real watermark.
  dry_run  — carried through to the enqueued payload; execute_build skips
             the actual Agent Engine call and treats it as a deterministic
             success, so the rest of the pipeline is verifiable without
             Agent Engine being deployed or reliable yet.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timezone

from . import agent_client
from . import classify
from . import labels
from . import mail_utils
from . import notifications
from . import pubsub
from . import sheet
from . import storage

log = logging.getLogger("solutioning_agent.ingestion")

_NOISE_FILTER = "-category:promotions -category:social -in:chats"
_DRY_RUN_REPLY = "[dry-run] https://docs.google.com/presentation/d/DRY-RUN-NO-DECK-BUILT/edit"


def _self_filter() -> str:
    """Exclude the agent's own mail from its own sweep.

    The deck-built notification goes to SOLUTIONING_NOTIFY_EMAIL, currently
    the same mailbox this sweeps, so without this the agent reads its own
    notifications back as new requests. Confirmed live: one such
    notification classified as a solution request at confidence 1.00.
    """
    notify = os.environ.get("SOLUTIONING_NOTIFY_EMAIL", "").strip()
    return "-from:me" + (f" -from:{notify}" if notify else "")


def run_sweep_for_user(
    email: str, gmail, gmail_secret: str, *, force: bool = False, dry_run: bool = False
) -> dict:
    """One mailbox, one sweep. Returns a small summary for /sweep's response."""
    now = datetime.now(timezone.utc)
    cutoff = storage.bootstrap_cutoff() if force else storage.get_sweep_cutoff()

    branch_a_ids = _list_branch_a(gmail, cutoff)
    branch_b_ids = _list_branch_b(gmail)

    queued, rejected, skipped = 0, 0, 0

    for message_id in branch_a_ids:
        outcome = _process_branch_a(
            gmail, gmail_secret, message_id, force=force, dry_run=dry_run
        )
        queued, rejected, skipped = _tally(outcome, queued, rejected, skipped)

    for message_id in branch_b_ids:
        outcome = _process_branch_b(
            gmail, gmail_secret, message_id, force=force, dry_run=dry_run
        )
        queued, rejected, skipped = _tally(outcome, queued, rejected, skipped)

    if not force:
        # A test sweep must never move the real watermark — the next
        # genuine sweep still needs to cover whatever force skipped past.
        storage.set_sweep_watermark(now)

    return {
        "email": email,
        "mode": {"force": force, "dry_run": dry_run},
        "cutoff_used": cutoff.isoformat(),
        "branch_a_candidates": len(branch_a_ids),
        "branch_b_candidates": len(branch_b_ids),
        "queued_for_build": queued,
        "rejected": rejected,
        "skipped": skipped,
    }


def _tally(outcome: str, queued: int, rejected: int, skipped: int) -> tuple[int, int, int]:
    if outcome == "queued":
        return queued + 1, rejected, skipped
    if outcome == "rejected":
        return queued, rejected + 1, skipped
    return queued, rejected, skipped + 1


def _list_branch_a(gmail, cutoff: datetime) -> list[str]:
    query = (
        f"in:inbox {_NOISE_FILTER} {_self_filter()} "
        f"after:{int(cutoff.timestamp())}"
    )
    resp = gmail.users().messages().list(userId="me", q=query).execute()
    return [m["id"] for m in resp.get("messages", [])]


def _list_branch_b(gmail) -> list[str]:
    query = f"label:{labels.GENERATE_DECK_LABEL}"
    resp = gmail.users().messages().list(userId="me", q=query).execute()
    return [m["id"] for m in resp.get("messages", [])]


def _thread_lock_reason(existing_thread: dict | None, triggered_by: str) -> str | None:
    """None means proceed, otherwise the reason it's blocked.

    See docs/EMAIL-POLLER-DESIGN.md "Thread locking" for the full rule table
    and rationale — this is the implementation of that table, not a second
    copy of it.
    """
    if existing_thread is None:
        return None
    status = existing_thread["status"]
    if status == "building":
        return "thread is currently building"
    if status == "built" and triggered_by != "branch_b":
        return "thread already built (use the manual generate-deck label to force a rebuild)"
    return None


def _process_branch_a(
    gmail, gmail_secret: str, message_id: str, *, force: bool, dry_run: bool
) -> str:
    if not force and storage.decision_exists(message_id):
        return "skipped"

    msg = mail_utils.fetch_message(gmail, message_id)

    # Always checked, regardless of force — force only re-runs classification;
    # it never bypasses the thread lock (see docs/EMAIL-POLLER-DESIGN.md).
    existing_thread = storage.thread_status(msg.thread_id)
    lock_reason = _thread_lock_reason(existing_thread, "branch_a")
    if lock_reason is not None:
        if not force:
            storage.record_decision(message_id, "thread_already_active", lock_reason)
        return "skipped"

    # Classify against the whole thread so far, not just this one message —
    # the substance (budget, timeline, a scope change) often lands in a
    # later reply, not whichever message happened to trip the filter first.
    thread_context = mail_utils.fetch_thread_context(gmail, msg.thread_id)
    result = classify.classify_and_extract(msg.subject, thread_context)
    storage.record_decision(
        message_id,
        "solution_request" if result.is_solution_request else "not_a_request",
        result.reason,
        confidence=result.confidence,
    )

    if not result.is_solution_request:
        return "rejected"

    _enqueue(
        gmail_secret, msg, thread_context,
        client_name=result.client_name, brief=result.brief,
        touchpoints=result.touchpoints, category=result.category,
        triggered_by="branch_a", dry_run=dry_run,
    )
    return "queued"


def _process_branch_b(
    gmail, gmail_secret: str, message_id: str, *, force: bool, dry_run: bool
) -> str:
    if not force and storage.decision_exists(message_id):
        return "skipped"

    msg = mail_utils.fetch_message(gmail, message_id)

    # See _thread_lock_reason — branch_b (an explicit human label) is the
    # one case allowed to override an already-'built' thread.
    existing_thread = storage.thread_status(msg.thread_id)
    lock_reason = _thread_lock_reason(existing_thread, "branch_b")
    if lock_reason is not None:
        if not force:
            storage.record_decision(message_id, "thread_already_active", lock_reason)
        return "skipped"

    thread_context = mail_utils.fetch_thread_context(gmail, msg.thread_id)
    result = classify.extract_only(msg.subject, thread_context)

    if not result.has_content:
        storage.record_decision(
            message_id, "manual_flag_insufficient",
            "labeled generate-deck but no usable content found",
        )
        return "rejected"

    storage.record_decision(
        message_id, "manual_flag_queued", "human-labeled generate-deck",
    )
    _enqueue(
        gmail_secret, msg, thread_context,
        client_name=result.client_name, brief=result.brief,
        touchpoints=result.touchpoints, category=result.category,
        triggered_by="branch_b", dry_run=dry_run,
    )
    return "queued"


def _enqueue(
    gmail_secret: str, msg: mail_utils.ParsedMessage, thread_context: str,
    client_name, brief, touchpoints, category, triggered_by: str, dry_run: bool,
) -> None:
    storage.open_thread(msg.thread_id, triggered_by)
    pubsub.publish_build_task(
        gmail_secret=gmail_secret,
        message_id=msg.message_id,
        thread_id=msg.thread_id,
        subject=msg.subject,
        thread_context=thread_context,
        received_at=msg.received_at.isoformat(),
        sender=msg.sender,
        rfc_message_id=msg.rfc_message_id,
        client_name=client_name,
        brief=brief,
        touchpoints=touchpoints,
        category=category,
        triggered_by=triggered_by,
        dry_run=dry_run,
    )


def _deck_link(reply: str) -> str | None:
    match = re.search(r"https://docs\.google\.com/presentation/d/[\w-]+", reply)
    return match.group(0) if match else None


def execute_build(payload: dict) -> str:
    """The /work side: one Pub/Sub message, one Agent Engine invocation.

    Pub/Sub is at-least-once — a redelivered message for an already-built
    or already-failed thread is acknowledged as a no-op, not reprocessed.
    """
    from ..auth.gmail_client import get_service_for_user  # avoids a circular import with app.main

    thread_id = payload["thread_id"]
    # Also the stale/redelivered-message guard: a genuine new trigger always
    # calls storage.open_thread (resetting status to 'building') before
    # publishing, so only a duplicate delivery of an already-completed task
    # still observes 'built'/'failed' here.
    existing = storage.thread_status(thread_id)
    if existing is not None and existing["status"] in ("built", "failed"):
        return existing["status"]

    gmail = get_service_for_user(payload["gmail_secret"])
    message_id = payload["message_id"]
    subject = payload["subject"]
    thread_context = payload["thread_context"]
    received_at = datetime.fromisoformat(payload["received_at"])

    if payload.get("dry_run"):
        # Deterministic, on purpose — no text-parsing of a model's reply.
        # The real fix for the non-dry-run path below is still a
        # structured tool result instead of string-matching a URL out of
        # free text; this sidesteps needing that fix while deck generation
        # is being built as a separate component.
        reply = _DRY_RUN_REPLY
    else:
        reply = agent_client.invoke_agent(
            f"An email thread came in. Subject: {subject}\n\n"
            f"Full thread so far:\n{thread_context}\n\n"
            f"The brief_id for this request is {thread_id} — pass it to every "
            "research tool so the retrieval is recorded against this brief.\n\n"
            "Research what you need to, then build a solution deck grounded in "
            "what you actually found. Report the evidence with its sources and "
            "say plainly what you could not establish.",
            session_user_id=f"ingestion-{thread_id}",
        )
        if "docs.google.com/presentation" not in reply:
            log.warning(
                "ingestion.build_failed",
                extra={"thread_id": thread_id, "message_id": message_id},
            )
            storage.mark_thread_failed(thread_id, reply[:500])
            return "failed"

    labels.apply_label(gmail, message_id, labels.DECK_GENERATED_LABEL)
    notifications.send_deck_notification(
        payload["client_name"],
        payload["brief"],
        deck_link=_deck_link(reply),
        evidence=reply,
        retrievals=storage.retrieval_summary(thread_id),
        allowed_urls=storage.retrieved_urls(thread_id),
    )
    sheet.append_row(
        client_name=payload["client_name"],
        brief=payload["brief"],
        touchpoints=payload["touchpoints"],
        category=payload["category"],
        month=sheet.month_label(received_at),
    )
    storage.mark_thread_sheet_written(thread_id)
    storage.mark_thread_built(thread_id, brief_id=thread_id)
    return "built"
