"""Index HT's past pitch decks into BigQuery for the agent's search.

    python -m agents.solutioning_agent.index_past_decks

Run daily by the solutioning-agent-past-decks trigger, and by hand after
decks are added. For each file in the past-decks folders, an unchanged file
keeps the rows it already has. A new or changed one is read slide by slide
(a PDF page with no text layer is read by Gemini), tagged (which part of
the deck each slide belongs to, the HT IPs and solution types there, and a
summary) and embedded. The whole table is then replaced in one load, so a
removed file drops out and a failed one keeps its old rows.
"""

from __future__ import annotations

import functools
import io
import json
import os
import posixpath
import re
import zipfile
from datetime import datetime, timezone

from google.cloud import bigquery

from .oauth_creds import get_credentials
from .tools import billing, past_decks, solution_types
from .tools.logs import event

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
FOLDERS = [f for f in os.environ.get("PAST_DECKS_FOLDER_ID", "").split(",") if f]
MODEL = os.environ.get("RESEARCH_MODEL", "gemini-2.5-flash")
MODEL_LOCATION = os.environ.get("MODEL_LOCATION", "global")

_PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_SLIDES = "application/vnd.google-apps.presentation"
_PDF = "application/pdf"
# A PDF page with less text than this is a picture of a slide: read it with Gemini.
_MIN_PAGE_CHARS = 20
_MAX_TAG_CHARS = 60_000

SCHEMA = [
    bigquery.SchemaField("deck_id", "STRING", "REQUIRED"),
    bigquery.SchemaField("deck_name", "STRING"),
    bigquery.SchemaField("link", "STRING"),
    bigquery.SchemaField("modified_time", "STRING"),
    bigquery.SchemaField("slide_no", "INT64", description="0 is the deck's summary"),
    bigquery.SchemaField("context", "STRING", description="deck name and the part of the deck"),
    bigquery.SchemaField("text", "STRING"),
    bigquery.SchemaField("ips", "STRING", "REPEATED"),
    bigquery.SchemaField("channels", "STRING", "REPEATED"),
    bigquery.SchemaField("embedding", "FLOAT64", "REPEATED"),
    bigquery.SchemaField("indexed_at", "TIMESTAMP"),
]


# --- reading a file ------------------------------------------------------------

def pptx_slides(data: bytes) -> list[str]:
    """Each slide's text, in the deck's own slide order."""
    z = zipfile.ZipFile(io.BytesIO(data))
    rels = z.read("ppt/_rels/presentation.xml.rels").decode("utf8", "ignore")
    targets = {}
    for rel in re.findall(r"<Relationship\b[^>]*>", rels):
        rid, target = re.search(r'\bId="([^"]+)"', rel), re.search(r'\bTarget="([^"]+)"', rel)
        if rid and target:
            targets[rid.group(1)] = target.group(1)
    order = re.findall(r'<p:sldId [^>]*r:id="(rId\d+)"', z.read("ppt/presentation.xml").decode("utf8", "ignore"))
    slides = []
    for rid in order:
        path = posixpath.normpath(posixpath.join("ppt", targets[rid]))
        xml = z.read(path).decode("utf8", "ignore")
        paras = (" ".join(re.findall(r"<a:t>([^<]*)</a:t>", p)).strip() for p in re.findall(r"<a:p>(.*?)</a:p>", xml, re.S))
        slides.append(_unescape("\n".join(p for p in paras if p)))
    return slides


def _unescape(text: str) -> str:
    return text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&apos;", "'")


def pdf_pages(data: bytes, read_picture) -> list[str]:
    """Each page's text; a page with no text layer goes to `read_picture`."""
    from pypdf import PdfReader, PdfWriter

    pages = []
    for page in PdfReader(io.BytesIO(data)).pages:
        text = (page.extract_text() or "").strip()
        if len(text) < _MIN_PAGE_CHARS:
            one = PdfWriter()
            one.add_page(page)
            buf = io.BytesIO()
            one.write(buf)
            text = read_picture(buf.getvalue())
        pages.append(text)
    return pages


@functools.cache
def _gemini():
    """One client for the run: a client made per call is closed by garbage
    collection while its request is still in flight."""
    from google import genai

    return genai.Client(vertexai=True, project=PROJECT, location=MODEL_LOCATION)


def read_picture(pdf_page: bytes) -> str:
    """The words on a slide that is only a picture."""
    from google.genai import types

    resp = _gemini().models.generate_content(
        model=MODEL,
        contents=[types.Part.from_bytes(data=pdf_page, mime_type=_PDF),
                  "Transcribe every word on this presentation slide, in reading order. "
                  "Text only, no commentary. If there is no text, reply with nothing."],
        config=types.GenerateContentConfig(temperature=0, labels=billing.labels("past-deck-index")),
    )
    return (resp.text or "").strip()


def _download(drive, f: dict) -> bytes:
    if f["mimeType"] == _SLIDES:
        return drive.files().export(fileId=f["id"], mimeType=_PPTX).execute()
    return drive.files().get_media(fileId=f["id"], supportsAllDrives=True).execute()


def slides_of(drive, f: dict) -> list[str]:
    data = _download(drive, f)
    return pdf_pages(data, read_picture) if f["mimeType"] == _PDF else pptx_slides(data)


# --- tagging ---------------------------------------------------------------------

_TAG_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "parts": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
            "first_slide": {"type": "INTEGER"},
            "last_slide": {"type": "INTEGER"},
            "title": {"type": "STRING"},
            "ips": {"type": "ARRAY", "items": {"type": "STRING"}},
            "channels": {"type": "ARRAY", "items": {"type": "STRING", "enum": list(solution_types.CHANNELS)}},
        }, "required": ["first_slide", "last_slide", "title", "ips", "channels"]}},
    },
    "required": ["summary", "parts"],
}

_TAG_INSTRUCTION = """You are indexing one of HT Media's past pitch decks so a
planner can find and reuse its ideas. Read the whole deck, then:
- summary: two or three sentences: who it was for, the brief, the solution.
- parts: split the deck into consecutive parts (cover, brief, insight, the
  big idea, each component or activation, the plan, credentials, closing),
  covering every slide once. Each part's title names what it is about,
  e.g. "HT PACE Principals' Meet integration" or "Print jacket innovation".
  A part that runs over several slides keeps one title even where a slide
  never repeats the name.
- ips: HT's own properties or IPs the part is about (e.g. HT PACE, Fresh on
  Campus, Anokhee Club, Hindustan Olympiad, Weekend Sorted, a Mint summit).
  Never the client's brands or another company's.
- channels: the solution types the part uses, from the list below.

""" + solution_types.describe_for_agent()


def tag(name: str, slides: list[str]) -> dict:
    from google.genai import types

    numbered, used = [], 0
    for i, text in enumerate(slides, 1):
        if used > _MAX_TAG_CHARS:
            break
        numbered.append(f"--- slide {i}\n{text}")
        used += len(text)
    resp = _gemini().models.generate_content(
        model=MODEL,
        contents=f"Deck: {name}\n\n" + "\n".join(numbered),
        config=types.GenerateContentConfig(
            system_instruction=_TAG_INSTRUCTION, temperature=0,
            response_mime_type="application/json", response_schema=_TAG_SCHEMA,
            labels=billing.labels("past-deck-index"),
        ),
    )
    return json.loads(resp.text)


def rows_for(f: dict, slides: list[str], tags: dict, now: str) -> list[dict]:
    """The table rows for one deck: its summary (slide 0), then each slide."""
    name = re.sub(r"\.(pptx|pdf)$", "", f["name"], flags=re.I)
    part_of = {}
    for part in tags.get("parts", []):
        for n in range(part["first_slide"], part["last_slide"] + 1):
            part_of.setdefault(n, part)
    base = {"deck_id": f["id"], "deck_name": name, "link": f["webViewLink"],
            "modified_time": f["modifiedTime"], "indexed_at": now}
    rows = [{**base, "slide_no": 0, "context": f"{name}\nSummary", "text": tags.get("summary", ""),
             "ips": sorted({ip for p in tags.get("parts", []) for ip in p["ips"]}),
             "channels": sorted({c for p in tags.get("parts", []) for c in p["channels"]})}]
    for n, text in enumerate(slides, 1):
        if not text.strip():
            continue
        part = part_of.get(n, {})
        rows.append({**base, "slide_no": n, "context": f"{name}\n{part.get('title', '')}".strip(),
                     "text": text, "ips": part.get("ips", []), "channels": part.get("channels", [])})
    vectors = past_decks.embed([f"{r['context']}\n{r['text']}"[:6000] for r in rows], "RETRIEVAL_DOCUMENT")
    for r, v in zip(rows, vectors):
        r["embedding"] = v
    return rows


# --- the run -------------------------------------------------------------------------

def _files(drive) -> list[dict]:
    files = []
    for folder in FOLDERS:
        page = None
        while True:
            resp = drive.files().list(
                q=f"'{folder}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'",
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, webViewLink)",
                pageSize=200, pageToken=page, supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            files += [f for f in resp.get("files", []) if f["mimeType"] in (_PPTX, _SLIDES, _PDF)]
            page = resp.get("nextPageToken")
            if not page:
                return files


def _existing(client: bigquery.Client) -> dict[str, list[dict]]:
    try:
        rows = client.query(f"SELECT * FROM `{past_decks.TABLE}`").result()
    except Exception:  # noqa: BLE001 - first run: no table yet
        return {}
    by_deck: dict[str, list[dict]] = {}
    for r in rows:
        row = dict(r)
        row["indexed_at"] = row["indexed_at"].isoformat() if row.get("indexed_at") else None
        by_deck.setdefault(row["deck_id"], []).append(row)
    return by_deck


def _index_one(f: dict, now: str) -> list[dict]:
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=get_credentials())  # one per thread
    slides = slides_of(drive, f)
    rows = rows_for(f, slides, tag(f["name"], slides), now)
    event("past_decks.deck_indexed", deck=f["name"], slides=len(slides))
    return rows


def main() -> None:
    from concurrent.futures import ThreadPoolExecutor

    from googleapiclient.discovery import build

    files = _files(build("drive", "v3", credentials=get_credentials()))
    client = bigquery.Client(project=PROJECT, default_query_job_config=billing.query_config())
    existing = _existing(client)
    now = datetime.now(timezone.utc).isoformat()
    unchanged = [f for f in files if existing.get(f["id"]) and existing[f["id"]][0]["modified_time"] == f["modifiedTime"]]
    changed = [f for f in files if f not in unchanged]
    rows = [r for f in unchanged for r in existing[f["id"]]]
    failed = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = {pool.submit(_index_one, f, now): f for f in changed}
        for future, f in futures.items():
            try:
                rows += future.result()
            except Exception as exc:  # noqa: BLE001 - one bad file never stops the rest
                rows += existing.get(f["id"], [])  # keep what it had
                failed += 1
                event("past_decks.deck_failed", "WARNING", deck=f["name"], error=str(exc)[:300])
    job = client.load_table_from_json(rows, past_decks.TABLE, job_config=bigquery.LoadJobConfig(
        schema=SCHEMA, write_disposition="WRITE_TRUNCATE", labels=billing.labels("past-deck-index")))
    job.result()
    event("past_decks.indexed", decks=len(files), indexed=len(changed) - failed, unchanged=len(unchanged),
          failed=failed, rows=len(rows))
    if failed:
        raise SystemExit(f"{failed} past deck(s) could not be indexed; see past_decks.deck_failed")


if __name__ == "__main__":
    main()
