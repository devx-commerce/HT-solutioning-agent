"""Gmail label helpers — the only thing that ever visibly touches a mailbox.

Two labels, opposite directions: `solutioning-agent/generate-deck` is applied by
a human and never touched by us; `solutioning-agent/deck-generated` is applied by
the agent, and only when a deck was actually built. Nothing else — not a
rejection, not "processed but nothing usable" — ever writes to Gmail.
"""

from __future__ import annotations

from functools import lru_cache

GENERATE_DECK_LABEL = "solutioning-agent/generate-deck"
DECK_GENERATED_LABEL = "solutioning-agent/deck-generated"


@lru_cache(maxsize=64)
def _label_id(gmail, label_name: str) -> str:
    """The label's id, creating it if this mailbox doesn't have it yet.

    Cached per (gmail client identity, label_name) for the life of the
    process — labels don't change id once created, no reason to look it up
    on every message.
    """
    existing = gmail.users().labels().list(userId="me").execute()
    for label in existing.get("labels", []):
        if label["name"] == label_name:
            return label["id"]

    created = (
        gmail.users()
        .labels()
        .create(
            userId="me",
            body={
                "name": label_name,
                "labelListVisibility": "labelShow",
                "messageListVisibility": "show",
            },
        )
        .execute()
    )
    return created["id"]


def apply_label(gmail, message_id: str, label_name: str) -> None:
    label_id = _label_id(gmail, label_name)
    gmail.users().messages().modify(
        userId="me", id=message_id, body={"addLabelIds": [label_id]}
    ).execute()
