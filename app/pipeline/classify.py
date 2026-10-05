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

_CLASSIFY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        # Answered first, on its own: a small model applies this test far more
        # reliably as a field than as a rule buried in the instruction.
        "only_rates_or_costing": types.Schema(
            type=types.Type.BOOLEAN,
            description="True if everything asked in the thread is about rates, rate cards, "
                        "costing, pricing, feasibility of a format or approvals, with no ask "
                        "for ideas, a plan or a solution.",
        ),
        "is_solution_request": types.Schema(type=types.Type.BOOLEAN),
        "reason": types.Schema(type=types.Type.STRING),
        "confidence": types.Schema(type=types.Type.NUMBER),
        "client_name": types.Schema(type=types.Type.STRING, nullable=True),
        "brief": types.Schema(type=types.Type.STRING, nullable=True),
        "touchpoints": types.Schema(
            type=types.Type.STRING, nullable=True, enum=TOUCHPOINTS
        ),
        "category": types.Schema(type=types.Type.STRING, nullable=True),
    },
    required=["only_rates_or_costing", "is_solution_request", "reason", "confidence"],
    property_ordering=["only_rates_or_costing", "is_solution_request", "reason", "confidence",
                       "client_name", "brief", "touchpoints", "category"],
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
class ClassifyResult:
    is_solution_request: bool
    reason: str
    confidence: float
    client_name: str | None
    brief: str | None
    touchpoints: str | None
    category: str | None


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


_CLASSIFY_INSTRUCTION = f"""
Decide whether this email is a genuine advertising/sponsorship/partnership
solutioning request that should produce a first-draft solution deck — as opposed to
an unrelated email, an internal note, a newsletter, or a reply that doesn't
itself constitute a new ask.

These are never requests, even when they name a client or brand:
- calendar invitations, acceptances and meeting updates;
- asks only for HT's rates, rate cards, pricing or media costs, with nothing
  asked about ideas, a plan or a solution; this includes HT's own sales and
  pricing teams discussing rates or packages between themselves;
- a thread forwarded with no new ask of its own, where the earlier messages
  are only about rates, approvals or scheduling.

If it is a request, also extract what the email actually states:
- client_name: only the client's brand or company name as written, such as
  "Haleon" or "Sheela Foam (Sleepwell)". Never an explanation of how you
  worked it out. Null if the email doesn't name one.
- brief: one or two sentences summarising the ask, in your own words.
- touchpoints: exactly one of {TOUCHPOINTS}, ONLY if the email unambiguously
  states the channel. Leave null if it's unclear or unstated — never guess.
- category: the client's industry/category, ONLY if the email states or
  makes it unambiguous. Leave null otherwise — never infer from the brand
  name alone.

Leave any field null rather than invent a value. A null field is a correct
answer, not a missing one.
""".strip()

_EXTRACT_INSTRUCTION = f"""
A human has already flagged this email as one that should produce a
solution deck — you are not deciding whether it's a request, only pulling
what's extractable from it.

Set has_content=false only if the email genuinely has nothing to build a
deck from (empty, unrelated content accidentally labeled, pure metadata).
Otherwise has_content=true and extract:
- client_name: only the client's brand or company name, never an
  explanation; null if not named.
- brief: one or two sentences summarising the ask.
- touchpoints: exactly one of {TOUCHPOINTS}, only if unambiguous, else null.
- category: only if unambiguous, else null.

Leave any field null rather than invent a value.
""".strip()


def classify_and_extract(subject: str, body: str) -> ClassifyResult:
    # Must bind to a variable, not chain _client().models.generate_content()
    # inline — the temporary Client object's refcount can hit zero mid
    # expression once .models is accessed, closing its internal httpx
    # client before generate_content() runs, which surfaces as "Cannot
    # send a request, as the client has been closed." Confirmed live
    # 2026-09-24 — classification had never actually worked until this.
    client = _client()
    response = client.models.generate_content(
        model=MODEL,
        contents=f"Subject: {subject}\n\nBody:\n{body}",
        config=types.GenerateContentConfig(
            system_instruction=_CLASSIFY_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=_CLASSIFY_SCHEMA,
            # No tools are ever passed here, so AFC has nothing to do —
            # disabling it avoids the SDK's own "not recommended" warning.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            labels=labels("classifier"),
        ),
    )
    data = json.loads(response.text)
    return ClassifyResult(
        is_solution_request=data["is_solution_request"] and not data.get("only_rates_or_costing"),
        reason=("only rates or costing asked: " if data.get("only_rates_or_costing") else "") + data["reason"],
        confidence=data.get("confidence", 0.0),
        client_name=clean_client_name(data.get("client_name")),
        brief=data.get("brief"),
        touchpoints=data.get("touchpoints"),
        category=data.get("category"),
    )


def extract_only(subject: str, body: str) -> ExtractResult:
    client = _client()  # see classify_and_extract's comment on why this must be bound
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
