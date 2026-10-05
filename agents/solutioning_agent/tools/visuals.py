"""Pictures for a deck: the two logos on the cover, and generated slide images.

The model writes Deck JSON but cannot produce image bytes, so it marks image
slots with a description (master_deck turns those into captioned
placeholders) and this module fills them just before rendering:

* the cover's top-right corner gets HT's logo from the brand asset folder in
  Drive and the client's logo read off the client's own website;
* every other placeholder gets an image generated from its description.

Anything that can't be filled stays a captioned placeholder, so a deck is
never blocked or broken for want of a picture: every function here returns
None or leaves the slot alone on failure, and never raises.
"""

from __future__ import annotations

import base64
import concurrent.futures
import html
import json
import logging
import os
import re
import struct
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from . import billing, master_deck

log = logging.getLogger("solutioning_agent.visuals")

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
# Checked 2026-10-01: no Imagen model (3.0 or 4.0, any variant) is
# reachable in this project, in us-central1 or global; gemini-2.5-flash-image
# is, in both.
IMAGE_LOCATION = os.environ.get("IMAGE_LOCATION", "global")
IMAGE_MODEL = os.environ.get("IMAGE_MODEL", "gemini-2.5-flash-image")

_USER_AGENT = "Mozilla/5.0 (compatible; HT-SolutioningAgent/1.0)"
_TIMEOUT = 10
_MAX_PAGE_BYTES = 2_000_000
_MAX_LOGO_BYTES = 2_000_000
# A logo smaller than this is a favicon, and prints as a blur on the cover;
# one larger is a banner or photo that merely has "logo" in its name.
_MIN_LOGO_LONG_SIDE, _MIN_LOGO_SHORT_SIDE = 120, 48
_MAX_LOGO_LONG_SIDE = 2000
# Words in an image's name or alt that mean it isn't the site's own mark.
_NOT_THE_LOGO = re.compile(
    r"background|banner|wall|hero|partner|client|award|sponsor|footer-bg|\bbg\b"
)

# Generated images live inside the stored Deck JSON, which is written to
# BigQuery as one query parameter (10 MB request limit). Past this budget the
# remaining slots stay placeholders rather than risk failing the save.
_IMAGE_BUDGET_CHARS = 6_000_000
_MAX_IMAGES_PER_DECK = 10
_IMAGE_WORKERS = 4

# Supported ratios the slots map to; the 12:13 two-column frame takes a
# square and the renderer crops it to fill.
_GENERATION_RATIO = {"16:9": "16:9", "12:13": "1:1"}
_STYLE = (
    "High-quality editorial photograph for a professional Indian media "
    "company's sales presentation. Natural light, realistic, uncluttered "
    "composition. Full-bleed: the scene fills the whole frame edge to edge, "
    "with no border, frame, mat or white margin. Avoid written text, "
    "captions and watermarks unless the description asks for them."
)


# --- HT's logo ----------------------------------------------------------------

_ht_logo: str | None = None


def ht_logo() -> str | None:
    """HT's logo from the brand asset folder in Drive, as a data URI.

    Cached only once found: a Drive blip on the first deck must not leave
    every later deck from the same process with a placeholder.
    """
    global _ht_logo
    if _ht_logo:
        return _ht_logo
    folder = os.environ.get("HT_ASSETS_FOLDER_ID", "")
    if not folder:
        return None
    try:
        from googleapiclient.discovery import build

        from ..oauth_creds import get_credentials

        drive = build("drive", "v3", credentials=get_credentials())
        files = drive.files().list(
            q=f"'{folder}' in parents and trashed=false and mimeType contains 'image/'",
            fields="files(id,name,mimeType)", pageSize=50,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute().get("files", [])
        logo = next((f for f in files if "logo" in f["name"].lower()), None)
        if logo is None:
            return None
        data = drive.files().get_media(fileId=logo["id"]).execute()
        _ht_logo = f"data:{logo['mimeType']};base64,{base64.b64encode(data).decode('ascii')}"
        return _ht_logo
    except Exception:  # noqa: BLE001 - a missing logo must not block a deck
        log.warning("visuals.ht_logo_unavailable", exc_info=True)
        return None


# --- the client's logo ----------------------------------------------------------


def _image_type_and_size(data: bytes) -> tuple[str, int, int] | None:
    """(mime, width, height) for a PNG, JPEG or GIF; None for anything else.

    SVG and WebP are refused rather than embedded: Google Slides' pptx import
    drops or rasterises them unreliably, and a broken cover is worse than a
    placeholder.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        w, h = struct.unpack(">II", data[16:24])
        return "image/png", w, h
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        w, h = struct.unpack("<HH", data[6:10])
        return "image/gif", w, h
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            (length,) = struct.unpack(">H", data[i + 2:i + 4])
            # SOF0–SOF15 carry the frame size, except DHT/JPG/DAC.
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "image/jpeg", w, h
            i += 2 + length
    return None


_GENERIC_NAME_WORDS = {
    "the", "and", "india", "indian", "ltd", "limited", "pvt", "private",
    "group", "company", "co", "inc", "corp", "corporation", "media",
}


def _name_tokens(client_name: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (client_name or "").lower())
    return {w for w in words if len(w) >= 3 and w not in _GENERIC_NAME_WORDS}


class _LogoCandidates(HTMLParser):
    """Places a site names its own logo, in the order they're trusted.

    A page shows other companies' logos too (partners, clients, awards), so
    an <img> only counts when it is marked as a logo in its own attributes or
    filename (not merely sitting under an /images/logos/ folder) and it is
    either in the site's header or nav, or names the client.
    """

    def __init__(self, client_name: str = "") -> None:
        super().__init__()
        self.structured: list[str] = []   # schema.org Organization logo
        self.images: list[str] = []       # <img> marked as the site's logo
        self.touch_icons: list[str] = []  # apple-touch-icon, ~180 px
        self.icons: list[tuple[int, str]] = []
        self._name = _name_tokens(client_name)
        self._chrome_depth = 0
        self._in_ld_json = False
        self._ld_parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag in ("header", "nav"):
            self._chrome_depth += 1
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld_json, self._ld_parts = True, []
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            filename = urllib.parse.urlparse(src).path.rsplit("/", 1)[-1]
            marks = " ".join((a.get("alt", ""), a.get("class", ""), a.get("id", ""), filename)).lower()
            names_client = any(t in marks for t in self._name)
            if (src and "logo" in marks and not _NOT_THE_LOGO.search(marks)
                    and (self._chrome_depth or names_client)):
                self.images.append(src)
        elif tag == "link":
            rel = a.get("rel", "").lower()
            href = a.get("href", "")
            # WordPress site icons ("cropped-…") are square crops of a wide
            # logo, so they cut the name off.
            if not href or "/cropped-" in href:
                return
            if "apple-touch-icon" in rel:
                self.touch_icons.append(href)
            elif "icon" in rel:
                size = re.match(r"(\d+)", a.get("sizes", ""))
                self.icons.append((int(size.group(1)) if size else 0, href))

    def handle_data(self, data):
        if self._in_ld_json:
            self._ld_parts.append(data)

    def handle_endtag(self, tag):
        if tag in ("header", "nav") and self._chrome_depth:
            self._chrome_depth -= 1
        elif tag == "script" and self._in_ld_json:
            self._in_ld_json = False
            try:
                self._collect_ld(json.loads("".join(self._ld_parts)))
            except (ValueError, TypeError):
                pass

    def _collect_ld(self, node):
        if isinstance(node, list):
            for n in node:
                self._collect_ld(n)
        elif isinstance(node, dict):
            logo = node.get("logo")
            if isinstance(logo, str):
                self.structured.append(logo)
            elif isinstance(logo, dict) and isinstance(logo.get("url"), str):
                self.structured.append(logo["url"])
            for key in ("@graph", "publisher", "brand"):
                if key in node:
                    self._collect_ld(node[key])

    def ordered(self) -> list[str]:
        big_icons = [h for size, h in sorted(self.icons, reverse=True) if size >= 128]
        return [*self.structured, *self.images, *self.touch_icons, *big_icons]


def _get(url: str, limit: int) -> tuple[str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return resp.url or url, resp.read(limit + 1)


def client_logo(site_url: str, client_name: str = "") -> str | None:
    """The client's logo, read off their own site, as a data URI.

    Only the site's own markup is trusted (its structured-data logo, an image
    it marks as its logo, its touch icon), never an image search, so the logo
    can't be another company's. None when the site can't be read or offers
    nothing usable; the cover then shows a placeholder.
    """
    parsed = urllib.parse.urlparse((site_url or "").strip())
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    try:
        final_url, body = _get(site_url.strip(), _MAX_PAGE_BYTES)
        finder = _LogoCandidates(client_name)
        finder.feed(body[:_MAX_PAGE_BYTES].decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - an unreadable site is an ordinary miss
        log.info("visuals.client_site_unreadable", extra={"url": site_url})
        return None

    seen: set[str] = set()
    for raw in finder.ordered()[:6]:
        url = urllib.parse.urljoin(final_url, html.unescape(raw.strip()))
        if url in seen or urllib.parse.urlparse(url).scheme not in ("http", "https"):
            continue
        seen.add(url)
        try:
            _, data = _get(url, _MAX_LOGO_BYTES)
        except Exception:  # noqa: BLE001 - try the next candidate
            continue
        if len(data) > _MAX_LOGO_BYTES:
            continue
        kind = _image_type_and_size(data)
        if not kind:
            continue
        mime, w, h = kind
        if (max(w, h) < _MIN_LOGO_LONG_SIDE or min(w, h) < _MIN_LOGO_SHORT_SIDE
                or max(w, h) > _MAX_LOGO_LONG_SIDE):
            continue
        return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    return None


# A slow site trickling bytes keeps each read under the socket timeout, so a
# lookup can otherwise run far longer than any one timeout (one took 40 s).
_CLIENT_LOGO_DEADLINE = 25
_logo_pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)


def _client_logo_within_deadline(site_url: str, client_name: str) -> str | None:
    future = _logo_pool.submit(client_logo, site_url, client_name)
    try:
        return future.result(timeout=_CLIENT_LOGO_DEADLINE)
    except Exception:  # noqa: BLE001 - too slow or failed: a placeholder
        log.info("visuals.client_logo_timed_out", extra={"url": site_url})
        return None


def add_cover_logos(deck: dict, client_name: str, client_site: str) -> dict:
    """Put HT's and the client's logos in the cover's top-right corner.

    A logo already embedded is kept, so a revision doesn't re-fetch it or
    lose one that was found before. Returns which logos are real.
    """
    slides = deck.get("slides") or []
    if not slides or not isinstance(slides[0], dict) or slides[0].get("layout") != "title":
        return {"ht_logo": False, "client_logo": False}
    existing = slides[0].get("logos")
    existing = existing if isinstance(existing, list) else []

    def kept(i: int) -> str | None:
        if i < len(existing) and isinstance(existing[i], dict):
            image = existing[i].get("image")
            if isinstance(image, str) and image.startswith("data:image/"):
                return image
        return None

    ht = kept(0) or ht_logo()
    client = kept(1) or (_client_logo_within_deadline(client_site, client_name) if client_site else None)
    slides[0]["logos"] = [
        {"image": ht or "placeholder", "alt": "HT Media logo"},
        {"image": client or "placeholder", "alt": f"{client_name or 'Client'} logo"},
    ]
    return {"ht_logo": bool(ht), "client_logo": bool(client)}


# --- generated slide images -----------------------------------------------------

_generated: dict[tuple[str, str], str] = {}


def _first_image_bytes(resp) -> bytes | None:
    for candidate in getattr(resp, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline = getattr(part, "inline_data", None)
            if inline is not None and getattr(inline, "data", None):
                return inline.data
    return None


def generate_image(description: str, ratio: str) -> str | None:
    """One image for `description` at a supported ratio, as a JPEG data URI.

    Cached by (description, ratio) once generated, so a deck the renderer
    rejects and the agent resubmits doesn't pay for its images twice.
    """
    description = (description or "").strip()
    target = _GENERATION_RATIO.get(ratio)
    if not description or not target:
        return None
    key = (description, target)
    if key in _generated:
        return _generated[key]
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(vertexai=True, project=PROJECT, location=IMAGE_LOCATION)
        resp = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=f"{description}\n\n{_STYLE}",
            config=types.GenerateContentConfig(
                labels=billing.labels("images"),
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(
                    aspect_ratio=target,
                    # JPEG, not the default PNG: ~370 KB instead of ~2 MB,
                    # which is what lets a deck's images fit the budget above.
                    image_output_options=types.ImageConfigImageOutputOptions(
                        mime_type="image/jpeg", compression_quality=75
                    ),
                ),
            ),
        )
        data = _first_image_bytes(resp)
    except Exception:  # noqa: BLE001 - a failed image leaves a placeholder
        log.warning("visuals.image_generation_failed", exc_info=True)
        return None
    if not data:
        # Safety-filtered or empty: nothing to embed.
        log.info("visuals.image_filtered", extra={"description": description[:120]})
        return None
    uri = f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"
    _generated[key] = uri
    return uri


def _placeholder_slots(deck: dict, only=None) -> list[tuple[dict, str, str]]:
    """(slide, ratio, description) for every slide image still a placeholder.

    `only` limits it to those slide objects. Why HT slides are skipped:
    their one image slot is HT's logo, which is never generated.
    """
    allowed = None if only is None else {id(s) for s in only}
    slots = []
    for slide in deck.get("slides") or []:
        if not isinstance(slide, dict) or str(slide.get("notes", "")).startswith("[why-ht:"):
            continue
        if allowed is not None and id(slide) not in allowed:
            continue
        image = slide.get("image")
        if isinstance(image, str) and image.startswith(master_deck.PLACEHOLDER_PREFIX):
            ratio = image[len(master_deck.PLACEHOLDER_PREFIX):]
            alt = str(slide.get("imageAlt") or "")
            description = alt.split("): ", 1)[1] if alt.startswith("Image placeholder (") else alt
            if description.strip() and description.strip() != master_deck.DEFAULT_CAPTION:
                slots.append((slide, ratio, description))
    return slots


def fill_images(deck: dict, only=None) -> dict:
    """Replace placeholder slide images with generated ones, in place.

    Run after master_deck.enforce, which has already turned every image the
    model wrote into a placeholder carrying its description. `only`, when
    given, limits it to those slides (a revision's own). Returns how many
    were generated and how many remain placeholders.
    """
    slots = _placeholder_slots(deck, only)
    if not slots:
        return {"generated": 0, "placeholders": 0}
    todo = slots[:_MAX_IMAGES_PER_DECK]
    with concurrent.futures.ThreadPoolExecutor(max_workers=_IMAGE_WORKERS) as pool:
        results = list(pool.map(lambda s: generate_image(s[2], s[1]), todo))

    budget = _IMAGE_BUDGET_CHARS - len(json.dumps(deck))
    generated = 0
    for (slide, _, description), uri in zip(todo, results):
        if not uri or len(uri) > budget:
            continue
        slide["image"] = uri
        slide["imageAlt"] = description
        budget -= len(uri)
        generated += 1
    return {"generated": generated, "placeholders": len(slots) - generated}


# --- an image the person supplies -----------------------------------------------

# What each image slot accepts from a person's upload. A photo is cropped to
# fill its frame, so it must be close to the frame's shape or the crop cuts
# away what they wanted shown; and it must have enough pixels not to print
# soft at the frame's size.
_SLOT_RULES = {
    "two-column": {"ratio": 12 / 13, "label": "12:13 (nearly square)",
                   "ideal": "1100×1200 px", "min_w": 550, "min_h": 600},
    "image-hero": {"ratio": 16 / 9, "label": "16:9 (widescreen)",
                   "ideal": "1920×1080 px", "min_w": 1280, "min_h": 720},
}
# The most of a picture a cover crop may cut away before it is refused.
_MAX_CROP = 0.20
_MAX_UPLOAD_BYTES = 4_000_000


def _ratio_label(w: int, h: int) -> str:
    for name, r in (("16:9", 16 / 9), ("4:3", 4 / 3), ("3:2", 3 / 2), ("1:1", 1.0),
                    ("3:4", 3 / 4), ("2:3", 2 / 3), ("9:16", 9 / 16)):
        if abs(w / h - r) / r < 0.03:
            return name
    return f"{w / h:.2f}:1"


def check_upload(data: bytes, mime_hint: str, target: str) -> tuple[str | None, str]:
    """(data URI, "") when an uploaded image can go in `target`, else (None, why).

    `target` is a slide layout with an image slot ("two-column",
    "image-hero") or "logo". The reason is written to be passed straight to
    the person: what's wrong and what would work instead.
    """
    kind = _image_type_and_size(data)
    if kind is None:
        what = mime_hint or "this file"
        return None, (f"{what} isn't a format the deck can use. Send it as a PNG or "
                      "JPEG (SVG, WebP and HEIC can't be placed reliably in Google Slides).")
    mime, w, h = kind
    if len(data) > _MAX_UPLOAD_BYTES:
        return None, (f"The image is {len(data) / 1_000_000:.1f} MB; images up to "
                      f"{_MAX_UPLOAD_BYTES // 1_000_000} MB can be placed. Send a smaller "
                      "export of it.")
    uri = f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"
    if target == "logo":
        if max(w, h) < _MIN_LOGO_LONG_SIDE or min(w, h) < _MIN_LOGO_SHORT_SIDE:
            return None, (f"The logo is only {w}×{h} px, so it would print blurred on "
                          f"the cover. Send one at least {_MIN_LOGO_LONG_SIDE} px on its "
                          "longer side, ideally a PNG with a transparent background.")
        return uri, ""
    rule = _SLOT_RULES.get(target)
    if rule is None:
        return None, ("That slide's layout has no picture slot. Pictures go on a "
                      "two-column slide (right half) or an image-hero slide (full slide).")
    # Every problem at once, so a second upload isn't refused for the other.
    problems = []
    if w < rule["min_w"] or h < rule["min_h"]:
        problems.append(f"it is {w}×{h} px, too small for this slot, so it would look "
                        f"soft on screen (it needs at least {rule['min_w']}×{rule['min_h']} px)")
    crop = 1 - min(w / h, rule["ratio"]) / max(w / h, rule["ratio"])
    if crop > _MAX_CROP:
        problems.append(f"it is {_ratio_label(w, h)} ({w}×{h} px) but this slot is "
                        f"{rule['label']}, so filling it would crop away about "
                        f"{round(crop * 100)}% of the picture")
    if problems:
        return None, ("The image can't be used here: " + "; and ".join(problems) +
                      f". Send a {rule['label']} image, ideally {rule['ideal']}, or place "
                      "it on a slide whose frame matches its shape.")
    return uri, ""
