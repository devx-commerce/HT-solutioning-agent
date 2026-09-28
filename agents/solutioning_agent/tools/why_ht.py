"""The "Why HT Media" credentials slides, inserted on request.

The numbers are fixed, not generated: each one is copied from a credentials
slide HT's solutioning team already uses in past decks, with that slide's own
source line. A model asked to "add HT's reach" will otherwise produce
plausible figures that were never true, which is the one thing a client-facing
credentials slide cannot contain.

What is flexible is the selection. Every insert gets the opener and the
scale slide; the agent adds up to two market slides that fit the brief (an
English-metro brief gets the Hindustan Times print slide, a UP/Bihar brief
gets Hindustan, and so on). Layouts are fixed per slide so the module always
renders the same way.

Figures are only as current as the decks they came from; several rest on IRS
2019. Update them here, in one place, when the solutioning team refreshes
them; `SOURCES` records where each slide came from.
"""

from __future__ import annotations

import base64
import copy
import functools
import os

# Marks a slide as part of this module, so re-inserting replaces it rather than
# duplicating it. Kept in speaker notes, which the renderer carries into the
# pptx but never draws on the slide.
TAG = "[why-ht:{key}]"

ALWAYS = ("opener", "scale")

VARIANTS: dict[str, str] = {
    "english-print": "English metro and affluent audiences: Hindustan Times print",
    "hindi-heartland": "UP, Bihar, Jharkhand and Hindi-speaking audiences: Hindustan print and Live Hindustan digital",
    "digital": "English digital-led briefs: hindustantimes.com, LiveMint, YouTube (not Live Hindustan)",
    "delhi-ncr": "Delhi NCR campaigns: every HT platform's share of the city",
}

# Past decks each slide's content is taken from, for whoever refreshes it.
SOURCES: dict[str, str] = {
    "opener": "HT x Signify_Social Impact Initiative_18.5.26 (slide 35)",
    "scale": "HT x Signify (slides 35, 41)",
    "english-print": "HT x Signify (slide 37); Rocksport X HT Media Proposal (slide 28)",
    "hindi-heartland": "DPL2026 x HT Media Proposal (slide 3); Muthoot Finance Proposal (slide 5)",
    "digital": "HT_Media_x_Centrum_Integrated_Proposal_2026 (slide 3)",
    "delhi-ncr": "DPL2026 (slide 3); Harvest Gold Global Energy Race 2026 (slide 3); KIET X HT Media (slide 7)",
}

_SLIDES: dict[str, dict] = {
    "opener": {
        "layout": "two-column",
        "eyebrow": "Why HT Media",
        "heading": "India's only full-funnel media powerhouse",
        "body": (
            "HT Media reaches 350M+ Indians across 25+ platforms and 12 genres: "
            "print, digital, audio and on-ground. From bespoke activations to IPs "
            "of scale and stature, one partner carries a campaign end to end."
        ),
        "imageAlt": "HT Media logo",
    },
    "scale": {
        "layout": "stat-row",
        "eyebrow": "Why HT Media",
        "heading": "Scale across every platform",
        "stats": [
            {"value": "350M+", "label": "Indians reached across HT Media's platforms"},
            {"value": "60M", "label": "Readers"},
            {"value": "281M+", "label": "Monthly visits across the HT Group's digital network"},
            {"value": "547M+", "label": "Monthly page views across the network"},
        ],
        "lead": "Sources: HT Media; Comscore MMX Multi-Platform, India, Dec 2025.",
    },
    "english-print": {
        "layout": "stat-row",
        "eyebrow": "Hindustan Times",
        "heading": "Premium English readers HT reaches alone",
        "stats": [
            {"value": "86L+", "label": "Premium English readers"},
            {"value": "#1", "label": "English daily in Delhi, on AIR and TR"},
            {"value": "85%", "label": "Of HT readers from NCCS A households"},
            {"value": "75%+", "label": "Of HT readers in Delhi + Mumbai don't read TOI"},
        ],
        "lead": "Sources: IRS Q4 2019 (readership, NCCS A); UMVS 2025 (exclusivity).",
    },
    "hindi-heartland": {
        "layout": "stat-row",
        "eyebrow": "Hindustan",
        "heading": "The Hindi heartland's trusted daily",
        "stats": [
            {"value": "50 MN", "label": "Hindustan readers"},
            {"value": "#1", "label": "Daily in Bihar and Jharkhand"},
            {"value": "#1", "label": "Hindi site by video views (Live Hindustan)"},
            {"value": "4X", "label": "Response from Hindustan readers vs competitors"},
        ],
        "lead": "Sources: IRS 2019 (readership, rank); HT Media (video views, reader response).",
    },
    "digital": {
        "layout": "stat-row",
        "eyebrow": "HT Digital",
        "heading": "Reach at the moment of intent",
        "stats": [
            {"value": "35M", "label": "Monthly active users on hindustantimes.com"},
            {"value": "9.7M", "label": "Weekly active users on hindustantimes.com"},
            {"value": "44M", "label": "Monthly visitors on LiveMint"},
            {"value": "8.69M", "label": "YouTube subscribers"},
        ],
        "lead": "Source: HT Media, as presented in 2026 proposals.",
    },
    "delhi-ncr": {
        "layout": "data-table",
        "eyebrow": "Print: IRS 2019 · Digital: GA",
        "heading": "HT Media & Delhi NCR: an inextricable bond",
        "columns": ["Platform", "Reach", "Standing", "From Delhi NCR"],
        "rows": [
            ["Hindustan Times", "8.6 MN readers", "No.1 English daily, Delhi NCR", "~40%"],
            ["HT digital", "93 MN UVs", "", "~20%"],
            ["Hindustan", "50 MN readers", "No.2 Hindi daily, Indo-Gangetic belt", "~10%"],
            ["Mint", "650 K readers", "India's No.2 business daily", "~40%"],
            ["Mint digital", "56 MN UVs", "", "~20%"],
            ["Fever FM", "", "Delhi NCR's No.1 radio station", "~40%"],
        ],
    },
}


@functools.lru_cache(maxsize=1)
def _logo_data_uri() -> str | None:
    """The HT logo from the brand asset folder in Drive, as an embeddable image.

    None when the folder isn't configured or unreachable; the opener then
    shows a captioned placeholder instead of failing the insert.
    """
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
        return f"data:{logo['mimeType']};base64,{base64.b64encode(data).decode('ascii')}"
    except Exception:  # noqa: BLE001 - a missing logo must not block the slides
        return None


def slides_for(variants: list[str]) -> tuple[list[dict], list[str]]:
    """The module's slides for the chosen market variants, and any unknown ones.

    The opener and scale slides are always included; at most two variants.
    """
    chosen, unknown = [], []
    for v in variants:
        v = v.strip().lower()
        if not v:
            continue
        if v not in VARIANTS:
            unknown.append(v)
        elif v not in chosen:
            chosen.append(v)
    slides = []
    for key in [*ALWAYS, *chosen[:2]]:
        slide = copy.deepcopy(_SLIDES[key])
        slide["notes"] = f"{TAG.format(key=key)} Source deck: {SOURCES[key]}."
        if key == "opener":
            slide["image"] = _logo_data_uri() or "placeholder"
            # The frame isn't square; cropping to fill would cut into the roundel.
            slide["imageFit"] = "contain"
        slides.append(slide)
    return slides, unknown


def is_module_slide(slide: dict) -> bool:
    return isinstance(slide, dict) and str(slide.get("notes", "")).startswith("[why-ht:")


def insert(deck: dict, variants: list[str]) -> tuple[list[str], list[str]]:
    """Put the module into `deck` straight after the brief (slide 2).

    That is where HT's own decks put their credentials: of the nine past decks
    that carry them, six place them at slides 3–7, right after the brief (DPL,
    Harvest Gold, both Centrum decks, KIET, Muthoot).

    Any earlier copy of the module is removed first, so asking twice (or for a
    different market) replaces the slides instead of stacking them. Returns the
    keys inserted and any variant names that were not recognised.
    """
    new, unknown = slides_for(variants)
    slides = [s for s in deck.get("slides") or [] if not is_module_slide(s)]
    # After the title and the brief recap.
    at = min(2, len(slides))
    deck["slides"] = slides[:at] + new + slides[at:]
    return [s["notes"].split("]")[0][len("[why-ht:"):] for s in new], unknown
