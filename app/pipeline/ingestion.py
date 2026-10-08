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

See developer-docs/EMAIL-POLLER-DESIGN.md for the reasoning behind every decision
here — this module is the implementation of that doc, not a second copy.

Two testing knobs on the classify-and-enqueue half, both off by default:

  force    — ignore the watermark (use the bootstrap cutoff instead) and
             ignore decision_exists/thread_status, so every Branch A/B
             candidate gets reprocessed as if seen for the first time.
             Never advances the real watermark.
  dry_run  — carried through to the enqueued payload; execute_build skips
             the agent and stops there. It never labels an email, sends a
             "deck drafted" email or writes a sheet row.
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime, timezone

from . import agent_client
from . import classify
from . import labels
from . import mail_utils
from . import notifications
from . import prompts
from . import pubsub
from . import sheet
from . import storage
from ..logs import event

_NOISE_FILTER = "-category:promotions -category:social -in:chats"


# Internal senders whose mail is never a client brief (HR, payroll, IT, the
# internal newsletter, the HRMS's notifications). Excluded in the Gmail query itself, so they
# never reach classification. Classification already rejects them (checked
# against the real inbox 2026-09-25); this saves the model call and keeps
# them out of the decision log. Branch B is not filtered: a person applying
# the generate-deck label by hand is an explicit override.
_DEFAULT_EXCLUDED_SENDERS = (
    "hrtimes@hindustantimes.com",
    "ithelpdesk@hindustantimes.com",
    "noreply@darwinbox.in",
    "digests@darwinbox.in",
    "payroll@hindustantimes.com",
    "trending@hindustantimes.com",
    "itcommunication@hindustantimes.com",
)
# Set from config.yaml (settings.excluded_senders) at deploy time.
EXCLUDED_SENDERS = tuple(
    a.strip() for a in os.environ.get("EXCLUDED_SENDERS", ",".join(_DEFAULT_EXCLUDED_SENDERS)).split(",")
    if a.strip()
)


def _sender_filter() -> str:
    return " ".join(f"-from:{addr}" for addr in EXCLUDED_SENDERS)


def _self_filter() -> str:
    """Exclude the agent's own mail from its own sweep.

    The "deck drafted" notification is sent from AGENT_EMAIL into the very
    inbox the brief came from, so without this the agent reads its own
    notifications back as new requests. Confirmed live: one such
    notification classified as a solution request at confidence 1.00.
    """
    agent = os.environ.get("AGENT_EMAIL", "").strip()
    return "-from:me" + (f" -from:{agent}" if agent else "")


def run_sweep_for_user(
    email: str, gmail, gmail_secret: str, *, force: bool = False, dry_run: bool = False
) -> dict:
    """One mailbox, one sweep. Returns a small summary for /sweep's response."""
    now = datetime.now(timezone.utc)
    cutoff = storage.bootstrap_cutoff() if force else storage.get_sweep_cutoff(email)

    branch_a_ids = _list_branch_a(gmail, cutoff)
    branch_b_ids = _list_branch_b(gmail)

    queued, rejected, skipped = 0, 0, 0

    for message_id in branch_a_ids:
        outcome = _process_branch_a(
            gmail, gmail_secret, message_id, force=force, dry_run=dry_run, mailbox=email
        )
        queued, rejected, skipped = _tally(outcome, queued, rejected, skipped)

    for message_id in branch_b_ids:
        outcome = _process_branch_b(
            gmail, gmail_secret, message_id, force=force, dry_run=dry_run, mailbox=email
        )
        queued, rejected, skipped = _tally(outcome, queued, rejected, skipped)

    if branch_a_ids or branch_b_ids:
        event("sweep.found", inbox=email, new_emails=len(branch_a_ids), labelled=len(branch_b_ids),
              queued=queued, not_briefs=rejected, skipped=skipped, dry_run=dry_run or None)

    if not force:
        # A test sweep must never move the real watermark — the next
        # genuine sweep still needs to cover whatever force skipped past.
        storage.set_sweep_watermark(email, now)

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
        f"in:inbox {_NOISE_FILTER} {_self_filter()} {_sender_filter()} "
        f"after:{int(cutoff.timestamp())}"
    )
    resp = gmail.users().messages().list(userId="me", q=query).execute()
    return [m["id"] for m in resp.get("messages", [])]


def _list_branch_b(gmail) -> list[str]:
    query = f"label:{labels.GENERATE_DECK_LABEL}"
    resp = gmail.users().messages().list(userId="me", q=query).execute()
    return [m["id"] for m in resp.get("messages", [])]


# Google Calendar's subjects for invitations and replies to them.
_CALENDAR_SUBJECT = re.compile(
    r"^\s*(updated invitation|invitation|accepted|declined|tentatively accepted|"
    r"cancell?ed event|new event|updated event)( with note)?\s*:", re.IGNORECASE)


def _thread_text(gmail, thread_id: str, mailbox: str, message_id: str, only_new: bool = False) -> str:
    """The thread as the classifier and the agent read it: each email's own
    text, the owner's emails and the new one marked, then the text of any
    attachments (a brief is often sent as a document). only_new: the new
    email and its attachments alone, which is what a brief is decided on."""
    text = f"Inbox owner: {mailbox}\n\n" if mailbox else ""
    text += mail_utils.fetch_thread_context(gmail, thread_id, owner=mailbox, new_message_id=message_id,
                                            only_new=only_new)
    for filename, content in mail_utils.attachment_texts(gmail, thread_id, message_id if only_new else ""):
        text += f"\n\n---\n\nAttachment: {filename}\n\n{content}"
    return text


def _thread_lock_reason(existing_thread: dict | None, triggered_by: str) -> str | None:
    """None means proceed, otherwise the reason it's blocked.

    See developer-docs/EMAIL-POLLER-DESIGN.md "Thread locking" for the full rule table
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
    gmail, gmail_secret: str, message_id: str, *, force: bool, dry_run: bool, mailbox: str = ""
) -> str:
    if not force and storage.decision_exists(message_id):
        return "skipped"

    msg = mail_utils.fetch_message(gmail, message_id)

    # Always checked, regardless of force — force only re-runs classification;
    # it never bypasses the thread lock (see developer-docs/EMAIL-POLLER-DESIGN.md).
    existing_thread = storage.thread_status(msg.thread_id)
    lock_reason = _thread_lock_reason(existing_thread, "branch_a")
    if lock_reason is not None:
        if not force:
            storage.record_decision(message_id, "thread_already_active", lock_reason)
        event("email.skipped", inbox=mailbox, message_id=message_id, subject=msg.subject, reason=lock_reason)
        return "skipped"

    # A calendar invitation names clients and brands but is never a brief; no
    # need to ask the model.
    if _CALENDAR_SUBJECT.match(msg.subject or ""):
        if not force:
            storage.record_decision(message_id, "not_a_request", "calendar invitation", confidence=1.0)
        event("email.not_a_brief", inbox=mailbox, message_id=message_id, sender=msg.sender,
              subject=msg.subject, reason="calendar invitation")
        return "rejected"

    # Classify against the whole thread so far, not just this one message —
    # the substance (budget, timeline, a scope change) often lands in a
    # later reply, not whichever message happened to trip the filter first.
    # Decided on the new email alone; the whole thread is read only to write
    # the brief once it is one.
    decision = classify.decide(msg.subject, _thread_text(gmail, msg.thread_id, mailbox, message_id, only_new=True))
    storage.record_decision(
        message_id,
        "solution_request" if decision.is_solution_request else "not_a_request",
        decision.reason,
        confidence=decision.confidence,
    )

    if not decision.is_solution_request:
        event("email.not_a_brief", inbox=mailbox, message_id=message_id, sender=msg.sender,
              subject=msg.subject, reason=decision.reason, confidence=decision.confidence)
        return "rejected"

    thread_context = _thread_text(gmail, msg.thread_id, mailbox, message_id)
    result = classify.extract_only(msg.subject, thread_context)
    _enqueue(
        gmail_secret, msg, thread_context,
        client_name=result.client_name, brief=result.brief,
        touchpoints=result.touchpoints, category=result.category,
        triggered_by="branch_a", dry_run=dry_run, mailbox=mailbox,
    )
    return "queued"


def _process_branch_b(
    gmail, gmail_secret: str, message_id: str, *, force: bool, dry_run: bool, mailbox: str = ""
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
        event("email.skipped", inbox=mailbox, message_id=message_id, subject=msg.subject, reason=lock_reason)
        return "skipped"

    thread_context = _thread_text(gmail, msg.thread_id, mailbox, message_id)
    result = classify.extract_only(msg.subject, thread_context)

    if not result.has_content:
        storage.record_decision(
            message_id, "manual_flag_insufficient",
            "labeled generate-deck but no usable content found",
        )
        event("email.label_without_brief", "WARNING", inbox=mailbox, message_id=message_id, subject=msg.subject)
        return "rejected"

    storage.record_decision(
        message_id, "manual_flag_queued", "human-labeled generate-deck",
    )
    _enqueue(
        gmail_secret, msg, thread_context,
        client_name=result.client_name, brief=result.brief,
        touchpoints=result.touchpoints, category=result.category,
        triggered_by="branch_b", dry_run=dry_run, mailbox=mailbox,
    )
    return "queued"


def _enqueue(
    gmail_secret: str, msg: mail_utils.ParsedMessage, thread_context: str,
    client_name, brief, touchpoints, category, triggered_by: str, dry_run: bool,
    mailbox: str = "",
) -> None:
    storage.open_thread(msg.thread_id, triggered_by, received_at=msg.received_at)
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
        # Whose inbox the brief came from: the "deck drafted" email goes there.
        mailbox=mailbox,
    )
    event("brief.queued", inbox=mailbox, thread_id=msg.thread_id, client=client_name, sender=msg.sender,
          subject=msg.subject, picked_up="automatically" if triggered_by == "branch_a" else "generate-deck label",
          dry_run=dry_run or None)


def _deck_link(reply: str) -> str | None:
    match = re.search(r"https://docs\.google\.com/presentation/d/[\w-]+", reply)
    return match.group(0) if match else None


def _quietly(fn, *args):
    """A lookup or save that must never stop a finished deck being delivered."""
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001
        event("build.extra_failed", "WARNING", step=fn.__name__, error=str(exc)[:200])
        return None


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
        event("build.duplicate_ignored", thread_id=thread_id, status=existing["status"])
        return existing["status"]
    started = time.time()
    event("build.started", thread_id=thread_id, client=payload.get("client_name"), inbox=payload.get("mailbox"))

    gmail = get_service_for_user(payload["gmail_secret"])
    message_id = payload["message_id"]
    subject = payload["subject"]
    thread_context = payload["thread_context"]
    received_at = datetime.fromisoformat(payload["received_at"])

    if payload.get("dry_run"):
        # A test sweep never labels an email, sends a "deck drafted" email or
        # writes a sheet row: those would say a deck exists when none does.
        storage.mark_thread_failed(thread_id, "dry run: no deck built")
        event("build.dry_run", thread_id=thread_id, client=payload.get("client_name"))
        return "dry_run"

    build_started = datetime.now(timezone.utc)
    reply = agent_client.invoke_agent(
        prompts.brief_request(subject, thread_context, thread_id),
        session_user_id=f"ingestion-{thread_id}",
    )
    # The deck of record, not a link in the reply: the reply can mention an
    # older deck, and only a deck saved for this brief during this build
    # earns the label, the "deck drafted" email and the sheet row.
    deck_link = storage.built_deck_link(thread_id, _deck_link(reply), since=build_started)
    if not deck_link:
        event("build.failed", "ERROR", thread_id=thread_id, client=payload.get("client_name"),
              reason="no deck saved for this brief", agent_reply=reply[:300])
        storage.mark_thread_failed(thread_id, reply[:500])
        return "failed"

    labels.apply_label(gmail, message_id, labels.DECK_GENERATED_LABEL)
    ref = _quietly(storage.brief_ref, thread_id)
    _quietly(storage.save_report, thread_id, reply)
    notifications.send_deck_notification(
        # Builds queued before this field existed fall back to the agent's own inbox.
        payload.get("mailbox") or os.environ.get("AGENT_EMAIL", ""),
        payload["client_name"],
        payload["brief"],
        deck_link=deck_link,
        evidence=reply,
        retrievals=storage.retrieval_summary(thread_id),
        allowed_urls=storage.retrieved_urls(thread_id),
        refine_link=notifications.refinement_link(
            payload["client_name"], thread_id,
            payload.get("mailbox") or os.environ.get("AGENT_EMAIL", ""), brief_ref=ref,
        ),
        brief_ref=ref,
    )
    sheet.append_row(
        client_name=payload["client_name"],
        brief=payload["brief"],
        touchpoints=payload["touchpoints"],
        category=payload["category"],
        month=sheet.month_label(received_at),
        brief_ref=ref,
    )
    storage.mark_thread_sheet_written(thread_id)
    storage.mark_thread_built(thread_id, brief_id=thread_id)
    event("build.done", thread_id=thread_id, client=payload["client_name"], deck=deck_link,
          notified=payload.get("mailbox") or os.environ.get("AGENT_EMAIL", ""),
          build_minutes=round((time.time() - started) / 60, 1),
          minutes_since_email=round((datetime.now(timezone.utc) - received_at).total_seconds() / 60, 1))
    return "built"
