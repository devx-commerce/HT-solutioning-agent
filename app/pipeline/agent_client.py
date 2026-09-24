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
    reply_text = ""
    for event in engine.stream_query(
        user_id=session_user_id, session_id=session["id"], message=text
    ):
        reply_text += str(event)
    return reply_text
