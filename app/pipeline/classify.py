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
from dataclasses import dataclass

from google import genai
from google.genai import types

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
# gemini-2.0-flash-lite is not a valid model in this project/region — 404s
# every time (confirmed live 2026-09-24). gemini-2.5-flash-lite is the real
# cheap-tier model that actually exists here.
MODEL = os.environ.get("CLASSIFY_MODEL", "gemini-2.5-flash-lite")

# Touchpoints must map to one of these or stay blank — never a free-text
# guess. Category is deliberately the same shape even though whether it
# should be attempted at all is still open — see docs/EMAIL-POLLER-DESIGN.md.
TOUCHPOINTS = ["Print", "Digital", "Integrated", "Events"]

_CLASSIFY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
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


def _client() -> genai.Client:
    return genai.Client(vertexai=True, project=PROJECT, location=LOCATION)


_CLASSIFY_INSTRUCTION = f"""
Decide whether this email is a genuine advertising/sponsorship/partnership
solutioning request that should produce a first-draft solution deck — as opposed to
an unrelated email, an internal note, a newsletter, or a reply that doesn't
itself constitute a new ask.

If it is a request, also extract what the email actually states:
- client_name: the client or brand asking, if named.
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
- client_name, brief: as above.
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
        ),
    )
    data = json.loads(response.text)
    return ClassifyResult(
        is_solution_request=data["is_solution_request"],
        reason=data["reason"],
        confidence=data.get("confidence", 0.0),
        client_name=data.get("client_name"),
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
        ),
    )
    data = json.loads(response.text)
    return ExtractResult(
        has_content=data["has_content"],
        client_name=data.get("client_name"),
        brief=data.get("brief"),
        touchpoints=data.get("touchpoints"),
        category=data.get("category"),
    )
