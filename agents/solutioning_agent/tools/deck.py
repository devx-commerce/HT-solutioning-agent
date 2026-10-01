"""Deck tools: Deck JSON in, a Google Slides link out.

Deck JSON is the system of record, not the Slides file. HT has no Slides
template to copy, so decks are rendered by `presentation-md` (Deck JSON →
pptx) and uploaded to Drive, which converts the pptx to native Slides. A
revision patches the stored Deck JSON, re-renders, and replaces the file's
content via `files().update` — the same file id throughout, so links and
permissions already handed out keep working.

The agent owns the file; people get read access. Someone who wants to edit
takes their own copy, which keeps their edits out of the path a later
revision re-renders.

Lives inside the agent's own package because `adk deploy agent_engine`
bundles only this directory — see oauth_creds.py for the same constraint.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from datetime import datetime, timezone

import requests

from google.cloud import bigquery
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from ..oauth_creds import get_credentials
from . import master_deck, visuals, why_ht

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")
NODE_BIN = os.environ.get("NODE_BIN", "node")
RENDER_CLI = os.environ.get("PRESENTATION_MD_CLI", "")
# The renderer service (renderer/ in this repo, on Cloud Run). When set it is
# used instead of the local CLI: Agent Engine has no Node, so the deployed
# agent can only render over HTTP.
RENDER_URL = os.environ.get("RENDER_URL", "").rstrip("/")
DECK_FOLDER_ID = os.environ.get("DECK_FOLDER_ID", "")
DECK_READER_DOMAIN = os.environ.get("DECK_READER_DOMAIN", "")

_SLIDES_MIME = "application/vnd.google-apps.presentation"
_PPTX_MIME = (
    "application/vnd.openxmlformats-officedocument.presentationml.presentation"
)
_RENDER_TIMEOUT = 120
_UNAVAILABLE_MSG = (
    "The rendering service is unavailable, so nothing was published. The deck "
    "itself was NOT rejected: do not change it. Tell the person the deck could "
    "not be rendered right now and to ask again in a few minutes"
)


class DeckRenderError(RuntimeError):
    """presentation-md rejected the deck: the deck must change."""


class RendererUnavailable(RuntimeError):
    """The renderer couldn't be reached or failed on its own: the deck is fine."""


# Waits between attempts when the renderer is unavailable: a cold start or a
# brief outage, not a bad deck. Four attempts over about half a minute.
_RENDER_RETRY_WAITS = (2, 6, 15)
_RENDER_HTTP_TIMEOUT = 90


def _normalize_deck_json(deck_json: str) -> str:
    """Re-serialize so a literal newline inside a string value isn't fatal.

    Models routinely emit real control characters inside JSON strings, which
    strict parsers reject. `strict=False` accepts them, and re-dumping
    escapes them properly — worth doing because the alternative is the model
    burning a whole round-trip rediscovering it wrote invalid JSON.
    """
    try:
        return json.dumps(json.loads(deck_json, strict=False))
    except json.JSONDecodeError:
        return deck_json  # let the renderer report what's actually wrong


def _render_pptx(deck_json: str) -> bytes:
    """Deck JSON → pptx bytes: the renderer service if configured, else the CLI.

    Raises DeckRenderError when the deck itself is invalid, and
    RendererUnavailable when rendering couldn't happen at all, so callers can
    tell the agent whether to change the deck or just try again later.
    """
    if RENDER_URL:
        return _render_via_service(_normalize_deck_json(deck_json))
    return _render_via_cli(deck_json)


def _id_token_headers() -> dict:
    """An identity token for the private Cloud Run renderer.

    On Agent Engine this comes from the runtime's service account. A local
    renderer (http://localhost…) needs no token.
    """
    if RENDER_URL.startswith("http://localhost") or RENDER_URL.startswith("http://127."):
        return {}
    import google.auth.transport.requests
    from google.oauth2 import id_token

    token = id_token.fetch_id_token(google.auth.transport.requests.Request(), RENDER_URL)
    return {"Authorization": f"Bearer {token}"}


def _render_via_service(deck_json: str) -> bytes:
    last = "no attempt was made"
    for attempt, wait in enumerate((*_RENDER_RETRY_WAITS, None), start=1):
        try:
            resp = requests.post(
                f"{RENDER_URL}/render",
                data=deck_json.encode("utf-8"),
                headers={"content-type": "application/json", **_id_token_headers()},
                timeout=_RENDER_HTTP_TIMEOUT,
            )
        except (requests.ConnectionError, requests.Timeout) as exc:
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
        except Exception as exc:  # noqa: BLE001 - e.g. no identity token available
            last = f"{type(exc).__name__}: {str(exc)[:200]}"
        else:
            if resp.status_code == 200:
                return resp.content
            if resp.status_code in (400, 413, 422):
                # The deck is wrong. Retrying the same deck can't help.
                try:
                    body = resp.json()
                except ValueError:
                    body = {}
                details = body.get("details") or [body.get("message") or resp.text[:500]]
                raise DeckRenderError(
                    "Deck JSON is invalid:\n" + "\n".join(f"  - {d}" for d in details)
                )
            # 401/403 (not authorised yet), 429, 5xx: the renderer, not the deck.
            last = f"HTTP {resp.status_code}: {resp.text[:200]}"
        if wait is not None:
            time.sleep(wait)
    raise RendererUnavailable(f"after {len(_RENDER_RETRY_WAITS) + 1} attempts, {last}")


def _render_via_cli(deck_json: str) -> bytes:
    """Deck JSON → pptx bytes, via presentation-md's renderer CLI.

    The CLI validates against deck.schema.json before rendering, so an
    invalid deck fails here with the schema's own error text rather than
    producing a broken file.
    """
    deck_json = _normalize_deck_json(deck_json)
    if not RENDER_CLI:
        raise DeckRenderError(
            "PRESENTATION_MD_CLI is not set — no renderer available."
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "deck.pptx")
        proc = subprocess.run(
            # --no-attribution: no "Made with presentation-md" credit on client decks.
            [NODE_BIN, RENDER_CLI, "-f", "pptx", "-o", out, "--no-attribution"],
            input=deck_json.encode("utf-8"),
            capture_output=True,
            timeout=_RENDER_TIMEOUT,
        )
        if proc.returncode != 0 or not os.path.exists(out):
            raise DeckRenderError(
                (proc.stderr or proc.stdout).decode("utf-8", "replace").strip()[:1000]
            )
        with open(out, "rb") as fh:
            return fh.read()


def _upload_pptx(pptx: bytes, *, name: str, file_id: str) -> dict:
    """Create a new Slides file, or replace an existing one keeping its id."""
    drive = build("drive", "v3", credentials=get_credentials())
    with tempfile.NamedTemporaryFile(suffix=".pptx", delete=False) as fh:
        fh.write(pptx)
        path = fh.name
    try:
        media = MediaFileUpload(path, mimetype=_PPTX_MIME, resumable=False)
        if file_id:
            return drive.files().update(
                fileId=file_id, media_body=media, fields="id,webViewLink"
            ).execute()
        body = {"name": name, "mimeType": _SLIDES_MIME}
        if DECK_FOLDER_ID:
            body["parents"] = [DECK_FOLDER_ID]
        created = drive.files().create(
            body=body, media_body=media, fields="id,webViewLink"
        ).execute()
        _lock_to_readers(drive, created["id"])
        return created
    finally:
        os.unlink(path)


def _lock_to_readers(drive, file_id: str) -> None:
    """Read-only for everyone but the agent. Never fatal — a deck still beats no deck."""
    if not DECK_READER_DOMAIN:
        return
    try:
        drive.permissions().create(
            fileId=file_id,
            body={"type": "domain", "role": "reader", "domain": DECK_READER_DOMAIN},
            fields="id",
        ).execute()
    except Exception:  # noqa: BLE001 - surfaced by the deck simply not being shared
        pass


def _save_brief(brief_id: str, **fields) -> None:
    """MERGE, not a streaming insert.

    A streamed row sits in BigQuery's buffer for up to ~90 minutes and
    rejects UPDATE the whole time, which would break the very next revision.
    """
    now = datetime.now(timezone.utc).isoformat()
    sets = ", ".join(f"{k} = @{k}" for k in fields)
    cols = ", ".join(["brief_id", *fields, "created_at", "updated_at"])
    vals = ", ".join(["@brief_id", *(f"@{k}" for k in fields), "@now", "@now"])
    params = [
        bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in fields.items()
    ] + [
        bigquery.ScalarQueryParameter("brief_id", "STRING", brief_id),
        bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
    ]
    bigquery.Client(project=PROJECT).query(
        f"""
        MERGE `{PROJECT}.{DATASET}.briefs` T
        USING (SELECT @brief_id AS brief_id) S ON T.brief_id = S.brief_id
        WHEN MATCHED THEN UPDATE SET {sets}, updated_at = @now
        WHEN NOT MATCHED THEN INSERT ({cols}) VALUES ({vals})
        """,
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()


def build_solution_deck(
    deck_json: str, client_name: str, brief_id: str, client_website: str = ""
) -> dict:
    """Render a deck from Deck JSON and publish it as Google Slides.

    HT's and the client's logos are added to the cover, and every image slot
    is filled with a picture generated from its imageAlt description. Either
    falls back to a captioned placeholder when it can't be done.

    Args:
        deck_json: the complete deck as JSON, matching presentation-md's
            deck schema: an object with "type": "deck" and a "slides" array.
        client_name: who the deck is for; used to name the file.
        brief_id: the brief this deck belongs to. Pass "" when there isn't
            one and the deck's own file id becomes the brief id.
        client_website: the client's official homepage, exactly as the email
            thread or a search result gave it, for their logo. Pass "" when
            you couldn't establish it; never guess a domain.

    Returns:
        The deck's brief_id, file id and link, and which logos and images
        are real rather than placeholders; or an error explaining what the
        deck schema rejected.
    """
    # Normalize before both rendering and storing, so the stored copy is
    # always strictly parseable by a later update_deck.
    try:
        deck = json.loads(_normalize_deck_json(deck_json))
    except json.JSONDecodeError as exc:
        return {"error": f"Deck JSON could not be parsed, nothing was published: {exc}"}
    problems = master_deck.enforce(deck) + master_deck.first_draft_problems(deck)
    if problems:
        return {
            "error": "The deck breaks the HT master deck rules, nothing was "
            "published. Fix every item and call build_solution_deck again.",
            "problems": problems,
        }
    # Only once the deck passes the rules: a rejected deck shouldn't pay for
    # images it will be resubmitted with anyway. Pictures are never a reason
    # not to publish: if this fails outright, the deck ships with whatever
    # placeholders it has.
    try:
        pictures = {
            **visuals.add_cover_logos(deck, client_name, client_website),
            "images": visuals.fill_images(deck),
        }
    except Exception:  # noqa: BLE001
        visuals.log.exception("visuals.failed")
        pictures = {"pictures_error": "logos and images could not be added"}
    deck_json = json.dumps(deck)
    try:
        pptx = _render_pptx(deck_json)
    except (DeckRenderError, subprocess.SubprocessError) as exc:
        return {"error": f"Deck was not valid, nothing was published: {exc}"}
    except RendererUnavailable as exc:
        return {"error": _UNAVAILABLE_MSG + f" ({exc})"}

    created = _upload_pptx(
        pptx, name=f"HT Media × {client_name} solution deck", file_id=""
    )
    deck_id = created["id"]
    link = created.get("webViewLink") or (
        f"https://docs.google.com/presentation/d/{deck_id}/edit"
    )
    resolved_brief = brief_id or deck_id
    _save_brief(
        resolved_brief,
        client_name=client_name,
        status="drafted",
        deck_file_id=deck_id,
        deck_link=link,
        deck_json=deck_json,
    )
    return {"brief_id": resolved_brief, "deck_id": deck_id, "link": link, **pictures}


def update_deck(brief_id: str, edits_json: str) -> dict:
    """Change specific fields on specific slides of a deck already built.

    Everything not named in the edits is left exactly as it was; this
    patches the stored deck rather than regenerating it.

    Args:
        brief_id: which brief's deck to change, from build_solution_deck or
            lookup_deck. Never invented.
        edits_json: a JSON array of edits, each
            {"slide_index": 0-based int, "field": name, "value": new value}.
            A text field takes a string. A list field (rows, cards, steps,
            stats) takes the whole new list: copy it from get_deck_outline
            and change only what was asked. For example:
            [{"slide_index": 2, "field": "heading", "value": "Festive reach"}].
            Every call publishes to the real deck; never send a trial edit.

    Returns:
        The deck's link and which edits were applied, or an error naming
        what could not be found.
    """
    stored = _load_brief(brief_id)
    if not stored or not stored.get("deck_json"):
        return {"error": f"No stored deck found for brief_id {brief_id}."}
    try:
        edits = json.loads(edits_json)
        deck = json.loads(stored["deck_json"])
    except json.JSONDecodeError as exc:
        return {"error": f"Could not read the edits or the stored deck: {exc}"}
    if not isinstance(edits, list):
        return {"error": 'edits_json must be a JSON array of {"slide_index", "field", "value"}.'}

    slides = deck.get("slides") or []
    applied = []
    for edit in edits:
        if not isinstance(edit, dict):
            return {"error": f'Each edit must be an object, got: {edit!r}'}
        index, field = edit.get("slide_index"), edit.get("field")
        if not isinstance(index, int) or not 0 <= index < len(slides):
            return {"error": f"slide_index {index} is outside this deck's {len(slides)} slides."}
        if not field:
            return {"error": "Every edit needs a field name."}
        slides[index][field] = edit.get("value")
        applied.append(f"slide {index}: {field}")

    result = _republish(brief_id, stored, deck)
    if "error" in result:
        return result
    return {**result, "edits_applied": applied}


def add_why_ht_slides(brief_id: str, variants: str) -> dict:
    """Add HT Media's credentials slides ("Why HT") to a deck already built.

    The slides carry fixed, sourced HT figures. Never write HT's reach or
    rankings into a deck any other way. An opener and a scale slide are always
    added; choose up to two market slides that fit the brief. Asking again
    replaces the earlier set instead of adding a second one.

    Args:
        brief_id: which brief's deck to change, from build_solution_deck or
            lookup_deck. Never invented.
        variants: comma-separated market slides, up to two, from
            english-print, hindi-heartland, digital, delhi-ncr. Pass "" for
            only the opener and scale slides.

    Returns:
        The deck's link and which credentials slides were inserted, or an
        error naming what went wrong.
    """
    stored = _load_brief(brief_id)
    if not stored or not stored.get("deck_json"):
        return {"error": f"No stored deck found for brief_id {brief_id}."}
    try:
        deck = json.loads(stored["deck_json"])
    except json.JSONDecodeError as exc:
        return {"error": f"The stored deck could not be read: {exc}"}
    inserted, unknown = why_ht.insert(deck, variants.split(","))
    if unknown:
        return {
            "error": f"Unknown variants {unknown}; choose from "
            f"{', '.join(why_ht.VARIANTS)}. The deck is unchanged."
        }
    result = _republish(brief_id, stored, deck)
    if "error" in result:
        return result
    return {**result, "why_ht_slides": inserted}


def _republish(brief_id: str, stored: dict, deck: dict) -> dict:
    """Check, re-render and replace a revised deck in place, keeping its file id."""
    problems = master_deck.enforce(deck)
    if problems:
        return {
            "error": "The change would break the HT master deck rules, the deck "
            "is unchanged.",
            "problems": problems,
        }
    # No logo lookup or image generation here: a revision changes exactly
    # the fields it names, and the pictures from the build are already in
    # the stored deck.
    updated_json = json.dumps(deck)
    try:
        pptx = _render_pptx(updated_json)
    except (DeckRenderError, subprocess.SubprocessError) as exc:
        return {"error": f"Change rejected, the deck is unchanged: {exc}"}
    except RendererUnavailable as exc:
        return {"error": _UNAVAILABLE_MSG + f" ({exc})"}

    _upload_pptx(pptx, name="", file_id=stored["deck_file_id"])
    _save_brief(brief_id, deck_json=updated_json, status="drafted")
    return {"brief_id": brief_id, "link": stored["deck_link"]}


def _load_brief(brief_id: str) -> dict | None:
    rows = list(
        bigquery.Client(project=PROJECT).query(
            f"""
            SELECT brief_id, client_name, deck_file_id, deck_link, deck_json
            FROM `{PROJECT}.{DATASET}.briefs`
            WHERE brief_id = @brief_id
            ORDER BY updated_at DESC LIMIT 1
            """,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("brief_id", "STRING", brief_id)
                ]
            ),
        ).result()
    )
    return dict(rows[0]) if rows else None


def _readable(value):
    """A slide's content with embedded images and internal tags elided."""
    if isinstance(value, dict):
        return {k: _readable(v) for k, v in value.items() if k != "notes"}
    if isinstance(value, list):
        return [_readable(v) for v in value]
    if isinstance(value, str) and value.startswith("data:image/"):
        return "(embedded image)"
    return value


def get_deck_outline(brief_id: str) -> dict:
    """List what's currently on each slide of a deck, with its index.

    update_deck addresses slides by index, and nobody should have to know
    the index of "the closing slide"; read the outline instead of asking.

    Args:
        brief_id: which brief's deck to describe, from build_solution_deck
            or lookup_deck.

    Returns:
        Each slide's 0-based index and its full content (every field,
        including table rows, cards, steps and stats), or an error if no
        stored deck exists for that brief. This is the only place to read a
        deck's content from.
    """
    stored = _load_brief(brief_id)
    if not stored or not stored.get("deck_json"):
        return {"error": f"No stored deck found for brief_id {brief_id}."}
    try:
        deck = json.loads(stored["deck_json"])
    except json.JSONDecodeError as exc:
        return {"error": f"The stored deck could not be read: {exc}"}
    return {
        "brief_id": brief_id,
        "link": stored.get("deck_link"),
        # Full content, not just headings: with only field names the agent
        # couldn't tell which table row said "Live Hindustan", and made trial
        # edits against the real deck to find out.
        "slides": [
            {"slide_index": i, **_readable(s)}
            for i, s in enumerate(deck.get("slides") or [])
        ],
    }


def lookup_deck(client_name: str) -> dict:
    """Find the most recent deck built for a client, so it can be edited.

    A conversation doesn't remember decks built in an earlier session, and
    the email flow builds decks outside any conversation at all. This is
    how an existing deck gets found again instead of duplicated.

    Args:
        client_name: the client name to search for. It matches the value
            passed to build_solution_deck, not a free-text query.

    Returns:
        The most recent matching deck's brief_id, file id and link, or a
        not-found status the agent should report rather than guess past.
    """
    rows = list(
        bigquery.Client(project=PROJECT).query(
            f"""
            SELECT brief_id, deck_file_id, deck_link, deck_json IS NOT NULL AS has_deck_json
            FROM `{PROJECT}.{DATASET}.briefs`
            WHERE LOWER(client_name) = LOWER(@client_name) AND deck_file_id IS NOT NULL
            ORDER BY updated_at DESC LIMIT 1
            """,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("client_name", "STRING", client_name)
                ]
            ),
        ).result()
    )
    if not rows:
        return {"found": False}
    row = rows[0]
    return {
        "found": True,
        "brief_id": row["brief_id"],
        "deck_id": row["deck_file_id"],
        "link": row["deck_link"],
        "editable": bool(row["has_deck_json"]),
    }
