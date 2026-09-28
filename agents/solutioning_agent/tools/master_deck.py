"""HT's master deck: the fixed spine, the approved layouts, and their limits.

Every deck the agent builds or revises passes through `enforce()` before it is
rendered. Anything the agent could get wrong silently (the theme, image
placeholders, quote marks) is normalized here. Anything that needs the agent's
judgement (too much text, a layout outside the approved set, a broken spine,
an em dash) is returned as a problem list, and nothing is published until the agent fixes it. Text is
never truncated: a cut-off sentence on a client slide is worse than a retry.

The limits are sized for presentation-md's 16:9 layouts at the ht-media
theme's type scale. They are the single source of truth: the agent's
instruction is generated from `describe_for_agent()`, so the rules the model
reads and the rules enforced here cannot drift apart.
"""

from __future__ import annotations

from dataclasses import dataclass, field

THEME = "ht-media"

# The renderer draws a captioned placeholder panel for any image value that is
# not a data URI, so a placeholder needs no generated asset: just this marker
# and a caption saying what belongs there. Swap in real images (or generated
# ones) later by replacing the value; nothing else changes.
PLACEHOLDER_PREFIX = "placeholder://"

MIN_SLIDES = 7
MAX_SLIDES = 14


@dataclass(frozen=True)
class ImageRule:
    ratio: str          # aspect ratio the slot is drawn at
    pixels: str         # the size a real image for this slot should be
    when_missing: str   # what the slide does without one


@dataclass(frozen=True)
class LayoutRule:
    use_for: str
    # field -> max characters, for top-level string fields
    text: dict[str, int]
    # list field -> (min items, max items, {item field -> max characters})
    lists: dict[str, tuple[int, int, dict[str, int]]] = field(default_factory=dict)
    image: ImageRule | None = None
    card_image: ImageRule | None = None


# Not approved: ranked-list (its PPTX bars split the rank and overflow with
# sentence-length labels), chart, custom-html, code, streak-grid, metric-ring.
LAYOUTS: dict[str, LayoutRule] = {
    "title": LayoutRule(
        use_for="Cover slide only: client × HT and the campaign name.",
        text={"eyebrow": 40, "heading": 70, "lead": 150},
    ),
    "section": LayoutRule(
        use_for="Divider between parts of the deck. One line, no body.",
        text={"number": 2, "heading": 50, "lead": 120},
    ),
    "two-column": LayoutRule(
        use_for="One idea explained: a heading, a paragraph, and either a "
        "pull-quote aside or an image filling the right half. The brief recap "
        "and next-steps slides always use the aside.",
        # Half-width heading: 42 characters is two lines at the theme's size.
        text={"eyebrow": 40, "heading": 42, "body": 420, "aside": 150, "imageAlt": 140},
        # The media frame is 5.8 × 6.3 in. Photos are cropped to fill it;
        # set imageFit "contain" for a logo so nothing is cut off.
        image=ImageRule(
            "12:13", "1100×1200 px",
            "Use the aside for a one-line takeaway instead of an image.",
        ),
    ),
    "feature-grid": LayoutRule(
        use_for="Parallel items of equal weight: campaign pillars, deliverables, "
        "platforms. 2 columns for 2–4 cards with longer copy, 3 for 3–6 cards. "
        "Text only: an image inside a small card can't be seen, so give it its "
        "own two-column or image-hero slide.",
        text={"eyebrow": 40, "heading": 75},
        lists={"cards": (2, 6, {"title": 40, "body": 170})},
    ),
    "stat-row": LayoutRule(
        use_for="2–4 real figures from your research, each with a short label, "
        "and the lead naming where they came from (\"Source: …\"). Never "
        "decorative numbers like \"100%\" or \"4 hubs\". If you have no "
        "sourced figures, use another layout.",
        text={"eyebrow": 40, "heading": 75, "lead": 140},
        lists={"stats": (2, 4, {"value": 9, "label": 70})},
    ),
    "timeline": LayoutRule(
        use_for="A campaign calendar: phases or weeks in order.",
        text={"eyebrow": 40, "heading": 75},
        # Step titles must stay on one line or they run into the body below.
        lists={"steps": (3, 5, {"title": 22, "body": 120})},
    ),
    "comparison": LayoutRule(
        use_for="Two options or before/after, side by side.",
        text={"eyebrow": 40, "heading": 75, "leftLabel": 30, "rightLabel": 30,
              "left": 260, "right": 260},
    ),
    "data-table": LayoutRule(
        use_for="A media plan or deliverables grid: rows of placements by "
        "platform, format and timing. Up to 5 columns and 7 rows.",
        text={"eyebrow": 40, "heading": 75},
        lists={"columns": (2, 5, {}), "rows": (1, 7, {})},
    ),
    "quote": LayoutRule(
        use_for="One line that reframes the brief: the big idea stated plainly. "
        "No attribution line.",
        text={"quote": 170},
    ),
    "image-hero": LayoutRule(
        use_for="The big idea shown as a visual, with a short headline over it.",
        text={"eyebrow": 40, "heading": 60, "lead": 130, "imageAlt": 140},
        image=ImageRule(
            "16:9", "1920×1080 px",
            "Always rendered with a captioned placeholder until a real image "
            "is supplied. Never drop the slide for want of one.",
        ),
    ),
    "logo-wall": LayoutRule(
        use_for="HT platforms or partner brands as a wall of logos. Logos "
        "only, never photos.",
        text={"eyebrow": 40, "heading": 75, "lead": 140},
        lists={"cards": (3, 8, {"title": 30, "body": 80, "imageAlt": 80})},
        card_image=ImageRule("1:1", "400×400 px", "Cards show the name only."),
    ),
    "closing": LayoutRule(
        use_for="Last slide only: thank you and the commercial note.",
        text={"eyebrow": 40, "heading": 60, "lead": 160},
    ),
}

# Cells in a data-table are short by nature; one limit covers them all.
_TABLE_CELL_MAX = 45

# Position rules. Middle slides use any approved layout except these.
_FIRST, _SECOND, _LAST = "title", "two-column", "closing"
_ENDS_ONLY = {"title", "closing"}


def _placeholder(ratio: str, caption: str) -> tuple[str, str]:
    caption = (caption or "").strip() or "image to be added"
    return (
        f"{PLACEHOLDER_PREFIX}{ratio}",
        f"Image placeholder ({ratio}): {caption}",
    )


def _is_real_image(value: str) -> bool:
    return value.startswith("data:image/")


def _normalize_image(obj: dict, rule: ImageRule) -> None:
    """Anything that isn't an embedded image becomes a captioned placeholder.

    The model can't produce image bytes, and a remote URL it writes is either
    invented or unfetchable at render time; both end up as a broken frame.
    """
    image = obj.get("image")
    if not isinstance(image, str) or not image.strip() or _is_real_image(image):
        return
    alt = obj.get("imageAlt") or ""
    if alt.startswith("Image placeholder ("):
        alt = alt.split("): ", 1)[-1]
    obj["image"], obj["imageAlt"] = _placeholder(rule.ratio, alt)


# Em dashes read as machine-written in a client deck. A spaced en dash is the
# same device in disguise; an unspaced one in a range ("Weeks 1–6") is not.
_DASHES = ("—", " – ")


def _dashed_fields(obj, path: str = "") -> set[str]:
    if isinstance(obj, str):
        return {path or "text"} if any(d in obj for d in _DASHES) else set()
    found: set[str] = set()
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k not in ("notes", "image", "imageAlt"):
                found |= _dashed_fields(v, f"{path}.{k}" if path else k)
    elif isinstance(obj, list):
        for j, v in enumerate(obj):
            found |= _dashed_fields(v, f"{path}[{j}]")
    return found


def enforce(deck: dict) -> list[str]:
    """Normalize `deck` in place and return every rule it still breaks.

    An empty list means the deck may be rendered.
    """
    problems: list[str] = []
    if not isinstance(deck, dict) or not isinstance(deck.get("slides"), list):
        return ['The deck must be an object with a "slides" array.']

    deck["type"] = "deck"
    meta = deck.setdefault("meta", {})
    if not isinstance(meta, dict):
        meta = deck["meta"] = {}
    meta["theme"] = THEME

    slides = deck["slides"]
    # The Why HT module is added on request on top of a finished deck, so it
    # doesn't count against the deck's own length.
    own = [s for s in slides if not str((s or {}).get("notes", "")).startswith("[why-ht:")]
    if not MIN_SLIDES <= len(own) <= MAX_SLIDES:
        problems.append(
            f"The deck has {len(own)} slides; it must have "
            f"{MIN_SLIDES}–{MAX_SLIDES}."
        )

    for i, slide in enumerate(slides):
        where = f"slide {i} ({slide.get('layout') if isinstance(slide, dict) else '?'})"
        if not isinstance(slide, dict):
            problems.append(f"slide {i} is not an object.")
            continue
        layout = slide.get("layout")
        rule = LAYOUTS.get(layout)
        if rule is None:
            problems.append(
                f"{where}: layout is not approved. Use one of: "
                f"{', '.join(LAYOUTS)}."
            )
            continue
        if layout in _ENDS_ONLY and i not in (0, len(slides) - 1):
            problems.append(f"{where}: {layout} may only be the first or last slide.")

        dashed = sorted(_dashed_fields(slide))
        if dashed:
            problems.append(
                f"{where}: {', '.join(dashed)} use an em dash or a spaced en dash. "
                "Rewrite with a comma, colon or full stop; unspaced ranges like "
                "\"Weeks 1–6\" are fine."
            )

        for name, limit in rule.text.items():
            value = slide.get(name)
            if isinstance(value, str) and len(value) > limit:
                problems.append(
                    f"{where}: {name} is {len(value)} characters; the limit is "
                    f"{limit}. Rewrite it shorter; do not split it across fields."
                )

        for name, (lo, hi, item_limits) in rule.lists.items():
            items = slide.get(name)
            if items is None:
                if lo:
                    problems.append(f"{where}: needs {name} ({lo}–{hi} items).")
                continue
            if not isinstance(items, list) or not lo <= len(items) <= hi:
                count = len(items) if isinstance(items, list) else "not a list"
                problems.append(f"{where}: {name} has {count}; it must have {lo}–{hi}.")
                continue
            for j, item in enumerate(items):
                if isinstance(item, dict):
                    for key, limit in item_limits.items():
                        value = item.get(key)
                        if isinstance(value, str) and len(value) > limit:
                            problems.append(
                                f"{where}: {name}[{j}].{key} is {len(value)} "
                                f"characters; the limit is {limit}."
                            )
                    if rule.card_image:
                        _normalize_image(item, rule.card_image)
                    elif item.get("image"):
                        problems.append(
                            f"{where}: {name}[{j}] has an image. Cards are text "
                            "only; put the image on its own two-column or "
                            "image-hero slide."
                        )
                elif name == "rows" and isinstance(item, list):
                    for cell in item:
                        if isinstance(cell, str) and len(cell) > _TABLE_CELL_MAX:
                            problems.append(
                                f"{where}: a table cell is {len(cell)} characters; "
                                f"the limit is {_TABLE_CELL_MAX}."
                            )
                            break

        if layout == "quote":
            # The layout draws its own curly quotes; the model often adds its
            # own. It also prefixes the attribution with an em dash, and the
            # quote slide states our idea, not a person's words, so drop it.
            if isinstance(slide.get("quote"), str):
                slide["quote"] = slide["quote"].strip().strip('"“”\'').strip()
            slide.pop("by", None)
        if layout == "stat-row" and not str(slide.get("lead") or "").strip():
            problems.append(
                f"{where}: a stat-row needs its source in lead, e.g. "
                "\"Source: IRS 2019\". Use another layout if the numbers have none."
            )

        if rule.image:
            if layout == "image-hero" and not slide.get("image"):
                slide["image"] = "placeholder"
            _normalize_image(slide, rule.image)
        elif slide.get("image"):
            problems.append(f"{where}: this layout does not take an image.")

    if slides and isinstance(slides[0], dict) and slides[0].get("layout") != _FIRST:
        problems.append("slide 0 must be the title slide (layout title).")
    if len(slides) > 1 and isinstance(slides[1], dict) and slides[1].get("layout") != _SECOND:
        problems.append(
            "slide 1 must recap the brief as a two-column slide: what the client "
            "asked for in body, the single hardest requirement as the aside."
        )
    # The brief and next-steps slides make a point in words; an image there is
    # a placeholder nobody can fill meaningfully ("calendar", "roadmap").
    for i, role in ((1, "brief"), (len(slides) - 2, "next-steps")):
        if 0 < i < len(slides) - 1 and isinstance(slides[i], dict) and slides[i].get("layout") == "two-column":
            if slides[i].get("image") or not str(slides[i].get("aside") or "").strip():
                problems.append(
                    f"slide {i} ({role}) must use an aside, not an image: one line "
                    "that states the point of the slide."
                )
    if slides and isinstance(slides[-1], dict) and slides[-1].get("layout") != _LAST:
        problems.append("The last slide must be the closing slide (layout closing).")
    return problems


def describe_for_agent() -> str:
    """The master-deck rules as prose, for the agent's instruction."""
    lines = [
        f"Every deck has {MIN_SLIDES}–{MAX_SLIDES} slides and follows this spine:",
        "  1. title: the cover. Eyebrow 'HT Media × <Client>', the campaign name as heading.",
        "  2. two-column: the brief. What the client asked for in body, the one "
        "hardest requirement as the aside.",
        "  (If the Why HT slides are added, they go here, straight after the brief.)",
        "  3…n. the solution, built only from the approved layouts below. Typically "
        "the insight, the big idea (quote or image-hero), the pillars (feature-grid), "
        "the plan (timeline or data-table), and prior HT work with its source when "
        "search_past_decks found any.",
        "  n+1. two-column: next steps and commercials, with an aside. Never invent "
        "prices; say costing will be shared by HT's pricing team.",
        "  last. closing.",
        "",
        "Never use an em dash (—) or a spaced en dash ( – ) anywhere in a deck. "
        "They read as machine-written. Use a comma, colon or full stop.",
        "",
        "Approved layouts, what each is for, and hard text limits in characters. "
        "A deck that breaks a limit is rejected with the exact fields to shorten; "
        "write to the limits the first time:",
    ]
    for name, rule in LAYOUTS.items():
        fields = ", ".join(f"{k} ≤{v}" for k, v in rule.text.items())
        lists = "; ".join(
            f"{k}: {lo}–{hi} items" + (
                " (" + ", ".join(f"{ik} ≤{iv}" for ik, iv in lim.items()) + ")" if lim else ""
            )
            for k, (lo, hi, lim) in rule.lists.items()
        )
        lines.append(f"  {name}: {rule.use_for} Fields: {fields}." + (f" {lists}." if lists else ""))
    lines += [
        "",
        "Images: only two-column (the right half, 12:13) and image-hero (full "
        "slide, 16:9) take one, plus logo-wall cards for logos. Never put an "
        "image inside a feature-grid card; give it its own slide. You cannot "
        "supply image files, so set \"image\": \"placeholder\" and write in "
        "imageAlt what the image should show; it renders as a captioned "
        "placeholder. Never write an image URL.",
    ]
    return "\n".join(lines)
