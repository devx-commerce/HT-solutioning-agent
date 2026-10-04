"""Appends one row per thread to the solutioning briefs sheet. Never edits one.

The sheet is a human-made artefact, same pattern as the Slides template —
created once, by hand, with the header row already in place, referenced by
id. This module only ever appends; it never reads the sheet back, and never
touches a row it's already written. Column order must match the sheet
exactly: Client, Solution Pillar, Category, Closure Status, AM/CH, GH,
Brief, Touchpoints, Month.
"""

from __future__ import annotations

import os
from datetime import datetime

from googleapiclient.discovery import build

from ..auth.oauth_creds import get_credentials

SHEET_ID = os.environ.get("BRIEFS_SHEET_ID", "")
SHEET_RANGE = os.environ.get("BRIEFS_SHEET_RANGE", "Sheet1!A:I")


def month_label(received_at: datetime) -> str:
    """'April'26' — from the triggering email's own arrival timestamp.

    Never LLM-derived. This is metadata, not something to extract or infer.
    """
    return f"{received_at.strftime('%B')}'{received_at.strftime('%y')}"


def append_row(
    client_name: str | None,
    brief: str | None,
    touchpoints: str | None,
    category: str | None,
    month: str,
) -> None:
    row = [
        client_name or "",
        "",  # Solution Pillar — never filled, no taxonomy to classify against
        category or "",  # tentative — see developer-docs/EMAIL-POLLER-DESIGN.md
        "",  # Closure Status — human-owned, never agent-written
        "",  # AM/CH — no reliable signal from mailbox arrival
        "",  # GH — same reasoning as AM/CH
        brief or "",
        touchpoints or "",
        month,
    ]
    sheets = build("sheets", "v4", credentials=get_credentials())
    sheets.spreadsheets().values().append(
        spreadsheetId=SHEET_ID,
        range=SHEET_RANGE,
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row]},
    ).execute()
