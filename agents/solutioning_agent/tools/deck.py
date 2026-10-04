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

import asyncio
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.request
from datetime import datetime, timezone

import requests

from google.cloud import bigquery
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from google.adk.tools.tool_context import ToolContext

from ..oauth_creds import get_credentials
from . import master_deck, research, visuals, why_ht

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
    """Read-only for everyone but the agent, in each reader domain.

    DECK_READER_DOMAIN is comma-separated: HT's people are on both
    hindustantimes.com and htdigital.in. One domain failing never stops the
    others, and never the deck: a deck still beats no deck.
    """
    for domain in (d.strip() for d in DECK_READER_DOMAIN.split(",")):
        if not domain:
            continue
        try:
            drive.permissions().create(
                fileId=file_id,
                body={"type": "domain", "role": "reader", "domain": domain},
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
    deck_json: str, client_name: str, brief_id: str, client_website: str = "",
    tool_context: ToolContext = None,
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
    # A guessed site could hand the cover another company's logo; a
    # placeholder is better than that.
    website = client_website if research.url_was_given(client_website, tool_context) else ""
    try:
        pictures = {
            **visuals.add_cover_logos(deck, client_name, website),
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
    """Revise a deck already built: change fields, and add, delete or move slides.

    Everything not named in the edits is left exactly as it was; this
    patches the stored deck rather than regenerating it. All edits in one
    call are applied together and published once, or not at all.

    Args:
        brief_id: which brief's deck to change, from build_solution_deck or
            lookup_deck. Never invented.
        edits_json: a JSON array of edits, applied in order. Each slide
            index refers to the deck as it stands after the edits before it.
            - Change a field: {"slide_index": 3, "field": "heading", "value": "..."}.
              A text field takes a string. A list field (rows, cards, steps,
              stats) takes the whole new list: copy it from get_deck_outline
              and change only what was asked.
            - Add a slide: {"op": "insert", "position": 5, "slide": {...}}.
              The new slide gets index 5 and later slides move down one.
            - Delete a slide: {"op": "delete", "slide_index": 7}.
            - Move a slide: {"op": "move", "slide_index": 7, "position": 4}.
              Afterwards it is at index 4.
            Pictures: to give a slide a new picture, set its "image" to
            "placeholder" and its "imageAlt" to a description of the new
            picture, in the same call; it is generated when the edit is
            published. An image shown in the outline as "(embedded image
            <id>)" can be kept or reused on another slide by writing that
            exact text as the value.
            Every call publishes to the real deck; never send a trial edit.

    Returns:
        The deck's link, which edits were applied and how many pictures were
        generated, or an error naming what could not be done (in which case
        nothing changed).
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
        return {"error": 'edits_json must be a JSON array of edits, e.g. '
                '[{"slide_index": 2, "field": "heading", "value": "..."}].'}

    images = _embedded_images(deck)
    slides = deck.get("slides") or []
    deck["slides"] = slides
    applied, touched = [], []
    for edit in edits:
        if not isinstance(edit, dict):
            return {"error": f"Each edit must be an object, got: {edit!r}. Nothing was changed."}
        outcome = _apply_edit(slides, edit, touched)
        if outcome.startswith("error: "):
            return {"error": outcome[len("error: "):] + " Nothing was changed."}
        applied.append(outcome)

    unknown = _restore_images(deck, images)
    if unknown:
        return {"error": f"These image references aren't in this deck: {', '.join(sorted(unknown))}. "
                "Copy them exactly from get_deck_outline. Nothing was changed."}

    result = _republish(brief_id, stored, deck, new_pictures_on=touched)
    if "error" in result:
        return result
    return {**result, "edits_applied": applied}


def _index_ok(value, upper: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < upper


def _apply_edit(slides: list, edit: dict, touched: list) -> str:
    """Apply one edit in place; a description of it, or "error: ..."."""
    op = edit.get("op", "set")
    index = edit.get("slide_index")
    if op == "set":
        field = edit.get("field")
        if not _index_ok(index, len(slides)):
            return f"error: slide_index {index} is outside this deck's {len(slides)} slides."
        if not field or not isinstance(field, str):
            return "error: Every field edit needs a field name."
        slides[index][field] = edit.get("value")
        touched.append(slides[index])
        return f"slide {index}: {field}"
    if op == "insert":
        position, slide = edit.get("position"), edit.get("slide")
        if not _index_ok(position, len(slides) + 1):
            return f"error: insert position {position} must be 0 to {len(slides)}."
        if not isinstance(slide, dict) or not slide.get("layout"):
            return "error: An insert needs a \"slide\" object with a \"layout\"."
        slides.insert(position, slide)
        touched.append(slide)
        return f"inserted slide {position} ({slide.get('layout')})"
    if op == "delete":
        if not _index_ok(index, len(slides)):
            return f"error: slide_index {index} is outside this deck's {len(slides)} slides."
        removed = slides.pop(index)
        return f"deleted slide {index} ({removed.get('layout') if isinstance(removed, dict) else '?'})"
    if op == "move":
        position = edit.get("position")
        if not _index_ok(index, len(slides)):
            return f"error: slide_index {index} is outside this deck's {len(slides)} slides."
        if not _index_ok(position, len(slides)):
            return f"error: move position {position} must be 0 to {len(slides) - 1}."
        slides.insert(position, slides.pop(index))
        return f"moved slide {index} to {position}"
    return f'error: Unknown op {op!r}; use "insert", "delete", "move", or leave op out to change a field.'


# Embedded images are elided from the outline (one is hundreds of KB), as a
# reference the agent can hand back. Without it, an edit copying a list that
# held an image back from the outline overwrote the real image with the
# outline's placeholder text.
_IMAGE_REF = re.compile(r"^\(embedded image ([0-9a-f]{10})\)$")


def _image_ref(uri: str) -> str:
    return f"(embedded image {hashlib.sha256(uri.encode('utf-8')).hexdigest()[:10]})"


def _embedded_images(value, found: dict | None = None) -> dict[str, str]:
    """Every embedded image in a deck, by the reference the outline shows."""
    found = {} if found is None else found
    if isinstance(value, dict):
        for v in value.values():
            _embedded_images(v, found)
    elif isinstance(value, list):
        for v in value:
            _embedded_images(v, found)
    elif isinstance(value, str) and value.startswith("data:image/"):
        found[_image_ref(value)] = value
    return found


def _restore_images(value, images: dict[str, str]) -> set[str]:
    """Swap outline image references back for the images, in place.

    Returns any reference that isn't in the deck, including the bare
    "(embedded image)" older outlines showed, which names no image at all.
    """
    unknown: set[str] = set()

    def fix(v):
        if isinstance(v, str) and v.startswith("(embedded image"):
            if _IMAGE_REF.match(v) and v in images:
                return images[v]
            unknown.add(v)
            return v
        if isinstance(v, dict):
            for k in v:
                v[k] = fix(v[k])
        elif isinstance(v, list):
            for i in range(len(v)):
                v[i] = fix(v[i])
        return v

    fix(value)
    return unknown


async def place_image_from_chat(
    brief_id: str, slide_index: int, target: str, tool_context: ToolContext
) -> dict:
    """Put an image the person attached in this chat into their deck.

    The image is checked first: format, size, resolution, and (for a slide
    picture) whether its shape fits the slot without cropping much of it
    away. If it doesn't fit, nothing changes and `rejected` says why in words
    to pass on to the person, including what would work instead.

    Args:
        brief_id: which brief's deck, from build_solution_deck or lookup_deck.
        slide_index: the slide to put it on, from get_deck_outline: a
            two-column slide (right half) or an image-hero slide. Pass 0
            with target "client_logo".
        target: "slide_image" to use it as that slide's picture, or
            "client_logo" to use it as the client's logo on the cover.

    Returns:
        The deck's link and what was placed; or `rejected` with the reason;
        or an error when no image was attached or the slide can't take one.
    """
    found = await _latest_uploaded_image(tool_context)
    if found is None:
        return {"error": "No image is attached in this conversation. Ask the person "
                "to attach it (PNG or JPEG) in the chat, then try again."}
    data, mime = found
    return await asyncio.to_thread(_place_image, brief_id, slide_index, target, data, mime)


def _place_image(brief_id: str, slide_index: int, target: str, data: bytes, mime: str) -> dict:
    if target not in ("slide_image", "client_logo"):
        return {"error": 'target must be "slide_image" or "client_logo".'}
    stored = _load_brief(brief_id)
    if not stored or not stored.get("deck_json"):
        return {"error": f"No stored deck found for brief_id {brief_id}."}
    try:
        deck = json.loads(stored["deck_json"])
    except json.JSONDecodeError as exc:
        return {"error": f"The stored deck could not be read: {exc}"}
    slides = deck.get("slides") or []

    if target == "client_logo":
        if not slides or slides[0].get("layout") != "title":
            return {"error": "This deck has no cover slide to put a logo on."}
        uri, why = visuals.check_upload(data, mime, "logo")
        if not uri:
            return {"rejected": why, "deck_unchanged": True}
        logos = slides[0].get("logos") if isinstance(slides[0].get("logos"), list) else []
        ht = logos[0] if logos else {"image": visuals.ht_logo() or "placeholder", "alt": "HT Media logo"}
        slides[0]["logos"] = [ht, {"image": uri, "alt": f"{stored.get('client_name') or 'Client'} logo"}]
        placed = "client logo on the cover"
    else:
        if not _index_ok(slide_index, len(slides)):
            return {"error": f"slide_index {slide_index} is outside this deck's {len(slides)} slides."}
        slide = slides[slide_index]
        uri, why = visuals.check_upload(data, mime, slide.get("layout", ""))
        if not uri:
            return {"rejected": why, "deck_unchanged": True}
        slide["image"] = uri
        slide["imageFit"] = "cover"
        slide["imageAlt"] = slide.get("imageAlt") or "Image supplied by the HT team"
        placed = f"picture on slide {slide_index}"

    result = _republish(brief_id, stored, deck)
    if "error" in result:
        return result
    return {**result, "placed": placed}


async def _latest_uploaded_image(tool_context) -> tuple[bytes, str] | None:
    """The newest image the person attached: this message, earlier ones, artifacts.

    How Gemini Enterprise hands an uploaded file to an Agent Engine agent is
    unconfirmed (developer-docs/open-items.md), so every shape ADK can deliver one in
    is tried: inline bytes on a message part, a file reference on one, or a
    saved artifact.
    """
    contents = [tool_context.user_content]
    session = getattr(tool_context, "session", None)
    for event in reversed(getattr(session, "events", None) or []):
        if getattr(event, "author", "") == "user":
            contents.append(getattr(event, "content", None))
    for content in contents:
        for part in reversed(getattr(content, "parts", None) or []):
            found = _image_from_part(part)
            if found:
                return found
    try:
        names = await tool_context.list_artifacts()
    except Exception:  # noqa: BLE001 - no artifact service configured
        names = []
    for name in reversed(names or []):
        try:
            part = await tool_context.load_artifact(name)
        except Exception:  # noqa: BLE001
            continue
        found = _image_from_part(part) if part else None
        if found:
            return found
    return None


def _image_from_part(part) -> tuple[bytes, str] | None:
    inline = getattr(part, "inline_data", None)
    if inline is not None and getattr(inline, "data", None):
        mime = getattr(inline, "mime_type", "") or ""
        if mime.startswith("image/") or visuals._image_type_and_size(inline.data):
            return inline.data, mime
    file_data = getattr(part, "file_data", None)
    uri = getattr(file_data, "file_uri", "") if file_data is not None else ""
    mime = getattr(file_data, "mime_type", "") if file_data is not None else ""
    if uri and (mime or "").startswith("image/"):
        try:
            if uri.startswith("gs://"):
                from google.cloud import storage

                bucket, _, name = uri[len("gs://"):].partition("/")
                data = storage.Client(project=PROJECT).bucket(bucket).blob(name).download_as_bytes()
            else:
                with urllib.request.urlopen(uri, timeout=20) as resp:
                    data = resp.read(visuals._MAX_UPLOAD_BYTES + 1)
            return data, mime
        except Exception:  # noqa: BLE001 - an unreadable reference is no image
            return None
    return None


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


# Past this the stored deck risks BigQuery's 10 MB request limit on save.
_MAX_STORED_DECK_CHARS = 8_000_000


def _republish(brief_id: str, stored: dict, deck: dict, new_pictures_on=()) -> dict:
    """Check, re-render and replace a revised deck in place, keeping its file id.

    Pictures are generated only for placeholder slots on `new_pictures_on`,
    the slides the revision itself added or changed: a revision changes what
    it names and nothing else, so untouched placeholders stay as they are.
    """
    problems = master_deck.enforce(deck)
    if problems:
        return {
            "error": "The change would break the HT master deck rules, the deck "
            "is unchanged.",
            "problems": problems,
        }
    pictures = {}
    if new_pictures_on:
        try:
            pictures = {"images": visuals.fill_images(deck, only=new_pictures_on)}
        except Exception:  # noqa: BLE001 - never a reason not to publish
            visuals.log.exception("visuals.failed")
    updated_json = json.dumps(deck)
    if len(updated_json) > _MAX_STORED_DECK_CHARS:
        return {"error": "The deck would be too large to store with this many "
                "images; the deck is unchanged. Remove or replace an image first."}
    try:
        pptx = _render_pptx(updated_json)
    except (DeckRenderError, subprocess.SubprocessError) as exc:
        return {"error": f"Change rejected, the deck is unchanged: {exc}"}
    except RendererUnavailable as exc:
        return {"error": _UNAVAILABLE_MSG + f" ({exc})"}

    _upload_pptx(pptx, name="", file_id=stored["deck_file_id"])
    _save_brief(brief_id, deck_json=updated_json, status="drafted")
    return {"brief_id": brief_id, "link": stored["deck_link"], **pictures}


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
        return _image_ref(value)
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
    """Find the decks already built for a client, so one can be edited.

    A conversation doesn't remember decks built in an earlier session, and
    the email flow builds decks outside any conversation at all. This is
    how an existing deck gets found again instead of duplicated.

    Args:
        client_name: the client's name. Matches any deck whose client name
            contains it, ignoring case, so "Tata" also finds "Tata Sampann".

    Returns:
        found, and the matching decks newest first (up to five), each with
        its brief_id, title, client, link and when it was last changed. When
        exactly one matches, its brief_id and link are also given at the top
        level. When several match, list them for the person and ask which
        one they mean; never pick one yourself.
    """
    rows = list(
        bigquery.Client(project=PROJECT).query(
            f"""
            SELECT brief_id, client_name, deck_file_id, deck_link, updated_at,
                   JSON_VALUE(deck_json, '$.slides[0].heading') AS title,
                   deck_json IS NOT NULL AS has_deck_json
            FROM `{PROJECT}.{DATASET}.briefs`
            WHERE LOWER(client_name) LIKE CONCAT('%', LOWER(@client_name), '%')
              AND deck_file_id IS NOT NULL
            QUALIFY ROW_NUMBER() OVER (PARTITION BY brief_id ORDER BY updated_at DESC) = 1
            ORDER BY updated_at DESC LIMIT 5
            """,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("client_name", "STRING", client_name.strip())
                ]
            ),
        ).result()
    )
    if not rows:
        return {"found": False}
    decks = [
        {
            "brief_id": r["brief_id"],
            "title": r["title"] or "",
            "client_name": r["client_name"],
            "link": r["deck_link"],
            "last_changed": r["updated_at"].strftime("%d %b %Y, %H:%M UTC") if r["updated_at"] else "",
            "editable": bool(r["has_deck_json"]),
        }
        for r in rows
    ]
    result = {"found": True, "decks": decks}
    if len(decks) == 1:
        result.update(brief_id=decks[0]["brief_id"], link=decks[0]["link"],
                      editable=decks[0]["editable"])
    return result
