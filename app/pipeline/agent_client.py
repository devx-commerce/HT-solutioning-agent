"""Calling the deployed Agent Engine resource directly — not through GE.

GE is one caller of this resource; the background poller is another. Shared
between app/main.py's /handle_message and ingestion.py, so both invocation
paths use exactly one code path into the agent.

Verify against your installed google-cloud-aiplatform's actual
`vertexai.agent_engines` signature before assuming a failure here is a
permissions problem — this API surface has moved across versions.
"""

from __future__ import annotations

import os

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
AGENT_ENGINE_RESOURCE = os.environ.get("AGENT_ENGINE_RESOURCE", "")


def invoke_agent(text: str, session_user_id: str = "poller") -> str:
    import vertexai
    from vertexai import agent_engines

    vertexai.init(project=PROJECT, location=LOCATION)
    engine = agent_engines.get(AGENT_ENGINE_RESOURCE)
    session = engine.create_session(user_id=session_user_id)
    return final_reply(
        engine.stream_query(user_id=session_user_id, session_id=session["id"], message=text)
    )


# Tools whose response carries the deck's link.
_DECK_TOOLS = {"build_solution_deck", "update_deck", "add_why_ht_slides"}


def final_reply(events) -> str:
    """The agent's answer: its text after the last tool call.

    Agent Engine streams every event (tool calls, tool responses, interim
    "I'll search for..." messages) as a dict. Concatenating str() of all of
    them, as this used to, handed the notification email a dump of Python
    dicts as its evidence. If the answer omits the link a deck tool returned,
    the link is appended so a deck that was built is never reported as failed.
    """
    texts: list[str] = []
    deck_link = None
    for event in events:
        parts = ((event or {}).get("content") or {}).get("parts") or []
        tool_parts = [p for p in parts if p.get("function_call") or p.get("function_response")]
        if tool_parts:
            texts = []
            for p in tool_parts:
                resp = p.get("function_response") or {}
                if resp.get("name") in _DECK_TOOLS:
                    link = (resp.get("response") or {}).get("link")
                    if link:
                        deck_link = link
            continue
        texts.extend(p["text"] for p in parts if p.get("text"))
    reply = "".join(texts).strip()
    if deck_link and "docs.google.com/presentation" not in reply:
        reply = (reply + f"\n\nDeck: {deck_link}").strip()
    return reply
