"""One-off: give decks built before brief references existed their reference,
name their Drive files after it, fill the briefs sheet's Brief ref and
Request type columns, and recover each deck's research report from the
deck-drafted email that was sent for it.

    python -m deploy.backfill_brief_refs            # show what would change
    python -m deploy.backfill_brief_refs --apply    # change it

Safe to run again: decks that already have a reference keep it.
"""

from __future__ import annotations

import base64
import collections
import os
import re
import sys

sys.path.insert(0, os.getcwd())
from dotenv import load_dotenv  # noqa: E402

load_dotenv("agents/solutioning_agent/.env")

from google.cloud import bigquery, logging_v2  # noqa: E402
from googleapiclient.discovery import build  # noqa: E402

from agents.solutioning_agent.oauth_creds import get_credentials  # noqa: E402
from agents.solutioning_agent.tools import brief_refs  # noqa: E402

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
TABLE = f"{PROJECT}.{os.environ.get('BQ_DATASET', 'solutioning_agent')}.briefs"
SHEET_ID = os.environ.get("BRIEFS_SHEET_ID") or "1q-U5t4d_F0KmWuKJcsccCmgAiqPgs1pUhbo9HIAcbzI"
_TEST_DECKS = {"HT Master Deck Reference"}  # a layout test deck, not a brief
_DECK_ID = re.compile(r"/presentation/d/([-\w]{25,})")


def _decks(bq):
    return [dict(r) for r in bq.query(
        f"""SELECT brief_id, ANY_VALUE(client_name) AS client_name, ANY_VALUE(deck_file_id) AS deck_file_id,
                   MIN(created_at) AS created, ANY_VALUE(brief_ref) AS brief_ref, LOGICAL_OR(report IS NOT NULL) AS has_report
            FROM `{TABLE}` WHERE deck_file_id IS NOT NULL GROUP BY brief_id ORDER BY created, brief_id""").result()]


def _sent_reports(gmail) -> dict[str, str]:
    """Deck file id -> the report part of the deck-drafted email sent for it."""
    out, page = {}, None
    while True:
        resp = gmail.users().messages().list(userId="me", q='in:sent subject:"Solution deck drafted"', maxResults=100, pageToken=page).execute()
        for m in resp.get("messages", []):
            msg = gmail.users().messages().get(userId="me", id=m["id"], format="full").execute()
            body = _plain(msg["payload"]) or ""
            deck = _DECK_ID.search(body)
            start = re.search(r"\n(EVIDENCE SUMMARY|==== RESEARCH ====)\n", body)
            end = body.find("\nSOURCES CHECKED")
            if deck and start and deck.group(1) not in out:
                out[deck.group(1)] = body[start.end():end if end > 0 else None].strip()
        page = resp.get("nextPageToken")
        if not page:
            return out


def _builds() -> list[tuple[str, str]]:
    """(client as written in the sheet, brief ID) for each email build, in the
    order the pipeline wrote its sheet row. The sheet's client is the one the
    email triage read, which can differ from the deck's: the Lakmē Fashion
    Week brief built a deck for the Italian Trade Agency."""
    entries = logging_v2.Client(project=PROJECT).list_entries(
        filter_='jsonPayload.event="build.done" AND timestamp>="2026-09-01T00:00:00Z"', order_by="timestamp asc", page_size=500)
    return [(e.payload.get("client") or "", e.payload.get("thread_id")) for e in entries if isinstance(e.payload, dict)]


def _plain(part):
    if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", "ignore")
    for p in part.get("parts", []) or []:
        text = _plain(p)
        if text:
            return text
    return None


def main(apply: bool) -> None:
    bq = bigquery.Client(project=PROJECT)
    creds = get_credentials()
    drive, sheets, gmail = (build("drive", "v3", credentials=creds), build("sheets", "v4", credentials=creds),
                            build("gmail", "v1", credentials=creds))
    decks = [d for d in _decks(bq) if d["client_name"] not in _TEST_DECKS]
    reports = _sent_reports(gmail)
    print(f"{len(decks)} decks; {sum(1 for d in decks if d['brief_ref'])} already have a reference; "
          f"{len(reports)} deck-drafted emails found")
    for d in decks:
        ref = d["brief_ref"]
        if not ref:
            ref = brief_refs.assign(bq, TABLE, d["brief_id"], d["client_name"]) if apply else f"(new) {brief_refs.client_label(d['client_name'])}"
        if apply:
            drive.files().update(fileId=d["deck_file_id"], body={"name": ref}, fields="id", supportsAllDrives=True).execute()
        report = reports.get(d["deck_file_id"])
        if report and not d["has_report"] and apply:
            bq.query(f"UPDATE `{TABLE}` SET report = @r WHERE brief_id = @id", job_config=bigquery.QueryJobConfig(query_parameters=[
                bigquery.ScalarQueryParameter("r", "STRING", report), bigquery.ScalarQueryParameter("id", "STRING", d["brief_id"])])).result()
        d["brief_ref"] = ref
        print(f"  {d['created']:%d %b} {d['client_name'][:30]:30} -> {ref:28} report: {'yes' if report or d['has_report'] else 'no'}")

    # The briefs sheet: each row is the build that wrote it (the next build
    # logged with the same client); failing that, the client's next deck.
    rows = sheets.spreadsheets().values().get(spreadsheetId=SHEET_ID, range="Sheet1!A1:K500").execute().get("values", [])
    series = []  # (a name for the client, its decks' references in date order)
    for d in decks:
        hit = next((s for s in series if brief_refs.same_client(s[0], d["client_name"])), None)
        if hit:
            hit[1].append(d["brief_ref"])
        else:
            series.append((d["client_name"], [d["brief_ref"]]))
    ref_of = {d["brief_id"]: d["brief_ref"] for d in decks}
    builds, taken = _builds(), set()
    updates, used = [{"range": "Sheet1!J1:K1", "values": [["Brief ref", "Request type"]]}], collections.Counter()
    for n, row in enumerate(rows[1:], start=2):
        if not row or (len(row) > 9 and row[9]):
            continue
        done = next((b for b in builds if b[0] == row[0] and b[1] not in taken and b[1] in ref_of), None)
        if done:
            taken.add(done[1])
            ref = ref_of[done[1]]
        else:
            hit = next((s for s in series if brief_refs.same_client(s[0], row[0])), None)
            key, refs = (hit[0], hit[1]) if hit else (row[0], [])
            ref = refs[used[key]] if used[key] < len(refs) else ""
            used[key] += 1
        updates.append({"range": f"Sheet1!J{n}:K{n}", "values": [[ref, "Custom solution"]]})
        print(f"  sheet row {n}: {row[0][:30]:30} -> {ref or '(no deck found)'}")
    if apply:
        sheets.spreadsheets().values().batchUpdate(spreadsheetId=SHEET_ID, body={"valueInputOption": "RAW", "data": updates}).execute()
    print("applied" if apply else "dry run: nothing changed (add --apply)")


if __name__ == "__main__":
    main("--apply" in sys.argv)
