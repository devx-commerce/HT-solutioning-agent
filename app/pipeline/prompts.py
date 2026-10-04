"""The message the email pipeline sends the agent for a new brief.

One function, used by ingestion and by the eval sets in evals/, so what the
agent is evaluated on is exactly what it sees in production.
"""

from __future__ import annotations


def brief_request(subject: str, thread_context: str, brief_id: str) -> str:
    return (
        f"An email thread came in. Subject: {subject}\n\n"
        f"Full thread so far:\n{thread_context}\n\n"
        f"The brief_id for this request is {brief_id} — pass it to every "
        "research tool so the retrieval is recorded against this brief.\n\n"
        "Research what you need to, then build a solution deck grounded in "
        "what you actually found. Report the evidence with its sources and "
        "say plainly what you could not establish."
    )
