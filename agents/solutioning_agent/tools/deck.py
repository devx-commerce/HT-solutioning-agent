"""Three tools: create a deck, find one you already made, and edit it.

Lives inside the agent's own package (not at the repo root, where an
earlier version of this file sat) because `adk deploy agent_engine` only
bundles the agent's own directory — nothing outside it makes it into the
deployed package, and there's no CLI flag to include extra local packages
(checked: `adk deploy agent_engine --help` has no such option). See
agents/solutioning_agent/oauth_creds.py for the same reasoning applied to
its own dependency.

The find/edit pair exists for one reason — refinement across separate chat
sessions. Inside a single GE conversation, the agent already remembers the
deck it just built; open a new session tomorrow (or refine something the
email flow produced) and that memory is gone. lookup_deck reads BigQuery to
recover the deck id from a name; update_deck edits the deck that's already
there instead of creating a duplicate.
"""

from __future__ import annotations

import os

from google.cloud import bigquery
from googleapiclient.discovery import build

from ..oauth_creds import get_credentials

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")


def build_solution_deck(client_name: str, note: str) -> dict:
    """Copy the template deck and replace its two placeholder tokens.

    Args:
        client_name: goes into the {{CLIENT_NAME}} placeholder.
        note: goes into the {{NOTES}} placeholder.

    Returns:
        The new deck's file id and a link to open it.
    """
    template_file_id = os.environ["TEMPLATE_FILE_ID"]
    creds = get_credentials()
    drive = build("drive", "v3", credentials=creds)
    slides = build("slides", "v1", credentials=creds)

    copy = drive.files().copy(
        fileId=template_file_id,
        body={"name": f"Solution Deck — {client_name}"},
    ).execute()
    deck_id = copy["id"]

    slides.presentations().batchUpdate(
        presentationId=deck_id,
        body={
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {"text": "{{CLIENT_NAME}}"},
                        "replaceText": client_name,
                    }
                },
                {
                    "replaceAllText": {
                        "containsText": {"text": "{{NOTES}}"},
                        "replaceText": note,
                    }
                },
            ]
        },
    ).execute()

    _record_brief(client_name, deck_id)

    return {
        "deck_id": deck_id,
        "link": f"https://docs.google.com/presentation/d/{deck_id}/edit",
    }


def _record_brief(client_name: str, deck_file_id: str) -> None:
    """One row per deck, so a later session can find it by name."""
    from datetime import datetime, timezone

    client = bigquery.Client(project=PROJECT)
    now = datetime.now(timezone.utc).isoformat()
    client.insert_rows_json(
        f"{PROJECT}.{DATASET}.briefs",
        [
            {
                "brief_id": deck_file_id,
                "message_id": None,
                "client_name": client_name,
                "status": "drafted",
                "attempts": 0,
                "detail": None,
                "deck_file_id": deck_file_id,
                "deck_link": f"https://docs.google.com/presentation/d/{deck_file_id}/edit",
                "created_at": now,
                "updated_at": now,
            }
        ],
    )


def lookup_deck(client_name: str) -> dict:
    """Find the most recent deck built for a client, so it can be edited.

    Args:
        client_name: the client name to search for — matches the value
            passed to build_solution_deck, not a free-text query.

    Returns:
        The most recent matching deck's id and link, or a not-found status
        if nothing matches — the agent should say so rather than guess.
    """
    client = bigquery.Client(project=PROJECT)
    query = f"""
        SELECT deck_file_id, deck_link
        FROM `{PROJECT}.{DATASET}.briefs`
        WHERE LOWER(client_name) = LOWER(@client_name)
        ORDER BY created_at DESC
        LIMIT 1
    """
    job = client.query(
        query,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("client_name", "STRING", client_name)
            ]
        ),
    )
    rows = list(job.result())
    if not rows:
        return {"found": False}
    return {"found": True, "deck_id": rows[0]["deck_file_id"], "link": rows[0]["deck_link"]}


def update_deck(deck_id: str, find_text: str, replace_text: str) -> dict:
    """Edit an existing deck in place — never creates a new one.

    Args:
        deck_id: from a prior build_solution_deck or lookup_deck call. Never
            invented — the agent must have it from one of those two tools.
        find_text: exact text already on the deck to replace.
        replace_text: what to put there instead.

    Returns:
        Confirmation and the deck's link.
    """
    creds = get_credentials()
    slides = build("slides", "v1", credentials=creds)
    slides.presentations().batchUpdate(
        presentationId=deck_id,
        body={
            "requests": [
                {
                    "replaceAllText": {
                        "containsText": {"text": find_text},
                        "replaceText": replace_text,
                    }
                }
            ]
        },
    ).execute()
    return {
        "updated": True,
        "link": f"https://docs.google.com/presentation/d/{deck_id}/edit",
    }
