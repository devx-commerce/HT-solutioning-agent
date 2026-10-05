"""One cheap-model call per candidate — classification and extraction in
the same pass for Branch A, extraction only for Branch B.

No keyword tier. The only thing standing between "is this a solutioning request"
and an actual model call is the free Gmail-side filter in ingestion.py.

Uses Vertex AI's ambient application-default credentials (the Cloud Run
service's own runtime identity) — a separate, simpler credential path from
everything in oauth_creds.py / gmail_client.py. No OAuth token involved.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass

from google import genai
from google.genai import types

from ..billing import labels

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
# Separate from LOCATION: Gemini 3.x is served only from `global` here.
MODEL_LOCATION = os.environ.get("MODEL_LOCATION", LOCATION)
# gemini-2.0-flash-lite is not a valid model in this project/region — 404s
# every time (confirmed live 2026-09-24). gemini-2.5-flash-lite is the real
# cheap-tier model that actually exists here.
MODEL = os.environ.get("CLASSIFY_MODEL", "gemini-2.5-flash-lite")

# Touchpoints must map to one of these or stay blank — never a free-text
# guess. Category is deliberately the same shape even though whether it
# should be attempted at all is still open — see developer-docs/EMAIL-POLLER-DESIGN.md.
TOUCHPOINTS = ["Print", "Digital", "Integrated", "Events"]

_DECIDE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "is_solution_request": types.Schema(type=types.Type.BOOLEAN),
        "reason": types.Schema(type=types.Type.STRING),
        "confidence": types.Schema(type=types.Type.NUMBER),
    },
    required=["is_solution_request", "reason", "confidence"],
)

_EXTRACT_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "has_content": types.Schema(type=types.Type.BOOLEAN),
        "client_name": types.Schema(type=types.Type.STRING, nullable=True),
        "brief": types.Schema(type=types.Type.STRING, nullable=True),
        "touchpoints": types.Schema(
            type=types.Type.STRING, nullable=True, enum=TOUCHPOINTS
        ),
        "category": types.Schema(type=types.Type.STRING, nullable=True),
    },
    required=["has_content"],
)


@dataclass
class Decision:
    is_solution_request: bool
    reason: str
    confidence: float


@dataclass
class ExtractResult:
    has_content: bool
    client_name: str | None
    brief: str | None
    touchpoints: str | None
    category: str | None


def clean_client_name(name: str | None) -> str | None:
    """The brand or company alone.

    A short bracket is part of the name ("Sheela Foam (Sleepwell)"); a longer
    one is the model explaining itself ("Haleon (implied by email address and
    brands mentioned)") and is dropped.
    """
    if not name:
        return None
    cleaned = re.sub(r"\s*\(([^)]*)\)", lambda m: m.group(0) if len(m.group(1).split()) <= 3 else "", name)
    return cleaned.strip() or None


def _client() -> genai.Client:
    return genai.Client(vertexai=True, project=PROJECT, location=MODEL_LOCATION)


_DECIDE_INSTRUCTION = """
You see one new email that arrived in a person's inbox: the inbox owner,
who works on HT Media's solutions team. You see its sender and recipients,
its own text (including any email forwarded inside it) and its attachments,
but not the rest of its thread: judge this email alone.

is_solution_request is true only if all three hold:
- The email asks the inbox owner, as a client, an agency or an HT
  colleague, for something. Forwarding a client's brief to the owner
  counts. An email that answers, delivers or updates a request the owner
  made (mocks, a page, a deck, content ideas) does not.
- What it asks for is a solution: ideas, a plan, a proposal or a deck for
  a client. An ask only for rates, rate cards, costing, pricing,
  feasibility of a format, approvals, scheduling or information does not
  count. Neither does a calendar invitation.
- The ask is in this email, not only in an earlier one it replies to.
""".strip()

_EXTRACT_INSTRUCTION = f"""
This email thread has already been judged to ask for a solution deck
(by the classifier, or by a person labelling it): you are not deciding
whether it's a request, only pulling what's extractable from it.

Set has_content=false only if the email genuinely has nothing to build a
deck from (empty, unrelated content accidentally labeled, pure metadata).
Otherwise has_content=true and extract:
- client_name: only the client's brand or company name as written, such as
  "Haleon" or "Sheela Foam (Sleepwell)"; never an explanation of how you
  worked it out; null if not named.
- brief: one or two sentences summarising the ask, in your own words.
- touchpoints: exactly one of {TOUCHPOINTS}, only if the thread states the
  channel unambiguously, else null; never guess.
- category: the client's industry, only if the thread states it or makes it
  unambiguous, else null; never infer it from the brand name alone.

Leave any field null rather than invent a value.
""".strip()


def decide(subject: str, new_email: str) -> Decision:
    """Is this one new email a solution request to the inbox owner?

    Judged on the new email alone: given a long thread, the model attributes
    an older message's ask to a short new reply ("6 inserts").
    """
    # Must bind to a variable, not chain _client().models.generate_content()
    # inline — the temporary Client object's refcount can hit zero mid
    # expression once .models is accessed, closing its internal httpx
    # client before generate_content() runs, which surfaces as "Cannot
    # send a request, as the client has been closed." Confirmed live
    # 2026-09-24 — classification had never actually worked until this.
    client = _client()
    response = client.models.generate_content(
        model=MODEL,
        contents=f"Subject: {subject}\n\n{new_email}",
        config=types.GenerateContentConfig(
            system_instruction=_DECIDE_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_DECIDE_SCHEMA,
            # The same email gets the same answer every time.
            temperature=0,
            # No tools are ever passed here, so AFC has nothing to do —
            # disabling it avoids the SDK's own "not recommended" warning.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            labels=labels("classifier"),
        ),
    )
    data = json.loads(response.text)
    return Decision(data["is_solution_request"], data["reason"], data.get("confidence", 0.0))


def extract_only(subject: str, body: str) -> ExtractResult:
    client = _client()  # see decide's comment on why this must be bound
    response = client.models.generate_content(
        model=MODEL,
        contents=f"Subject: {subject}\n\nBody:\n{body}",
        config=types.GenerateContentConfig(
            system_instruction=_EXTRACT_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_EXTRACT_SCHEMA,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            labels=labels("classifier"),
        ),
    )
    data = json.loads(response.text)
    return ExtractResult(
        has_content=data["has_content"],
        client_name=clean_client_name(data.get("client_name")),
        brief=data.get("brief"),
        touchpoints=data.get("touchpoints"),
        category=data.get("category"),
    )
