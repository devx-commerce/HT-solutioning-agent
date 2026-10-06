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

import os
import re
from dataclasses import dataclass, field

THEME = "ht-media"

# The renderer draws a captioned placeholder panel for any image value that is
# not a data URI, so a placeholder needs no generated asset: just this marker
# and a caption saying what belongs there. Swap in real images (or generated
# ones) later by replacing the value; nothing else changes.
PLACEHOLDER_PREFIX = "placeholder://"
# The caption a placeholder gets when the model described nothing; there is
# nothing to generate an image from.
DEFAULT_CAPTION = "image to be added"

MIN_SLIDES = 7
# Set from config.yaml (settings.max_slides, settings.max_images).
MAX_SLIDES = int(os.environ.get("MAX_SLIDES", "40"))
# The most pictures a deck may have, however long it is. A first draft also
# needs one picture for every three content slides and at most one for every
# two (picture_range); checked only when a deck is built, never on a
# revision, so decks built before the rule existed stay editable.
MAX_IMAGES = int(os.environ.get("MAX_IMAGES", "13"))


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
        use_for="One idea explained: a heading, 3 to 5 short points on the "
        "left, and either an image or a one-line takeaway (aside) on the right. "
        "Never two columns of text: for a side-by-side use comparison. The "
        "brief recap always uses the aside.",
        # Half-width heading: 42 characters is two lines at the theme's size.
        text={"eyebrow": 40, "heading": 42, "body": 600, "aside": 150, "imageAlt": 140},
        # The media frame is 5.8 × 6.3 in. Photos are cropped to fill it;
        # set imageFit "contain" for a logo so nothing is cut off.
        image=ImageRule(
            "12:13", "1100×1200 px",
            "Use the aside for a one-line takeaway instead of an image.",
        ),
    ),
    "feature-grid": LayoutRule(
        use_for="Parallel items of equal weight: campaign pillars, deliverables, "
        "platforms. 2, 3, 4 or 6 cards, never 5, so every row is full: 2 cards "
        "and 4 cards in 2 columns, 3 and 6 cards in 3 columns. Each card title "
        "fits one line: at most 26 characters in 3 columns. "
        "Text only: an image inside a small card can't be seen, so give it its "
        "own two-column or image-hero slide.",
        text={"eyebrow": 40, "heading": 75},
        lists={"cards": (2, 6, {"title": 40, "body": 260})},
    ),
    "at-a-glance": LayoutRule(
        use_for="A component's key facts as label: value rows, the ones that "
        "matter for it, from Platform, Format, Frequency, Duration, Geography, "
        "Scale (indicative), Who runs it, Measured by. 4 to 6 rows, one short "
        "value each. Optionally a picture of the component on the right.",
        text={"eyebrow": 40, "heading": 75, "lead": 140, "imageAlt": 140},
        lists={"facts": (4, 6, {"label": 24, "value": 90})},
        image=ImageRule(
            "12:13", "1100×1200 px",
            "The facts take the full width without one.",
        ),
    ),
    "options": LayoutRule(
        use_for="2 to 4 numbered choices or parts side by side: integration "
        "options, formats, episode or content ideas, phases. Each a short "
        "title and 2 or 3 points.",
        text={"eyebrow": 40, "heading": 75, "lead": 140},
        lists={"cards": (2, 4, {"title": 40, "body": 220})},
    ),
    "stat-row": LayoutRule(
        use_for="2–4 real figures from your research, each with a short label, "
        "and where they came from in source (small print at the foot, e.g. "
        "\"IRS 2019\"). Never decorative numbers like \"100%\" or \"4 hubs\". "
        "If you have no sourced figures, use another layout.",
        text={"eyebrow": 40, "heading": 75, "lead": 140, "source": 120},
        lists={"stats": (2, 4, {"value": 9, "label": 70})},
    ),
    "stat-story": LayoutRule(
        use_for="1 to 3 big numbers with the story behind them: a scale that "
        "impresses on the left (\"2,000+ schools\", \"100 to 120 RWAs across "
        "Delhi\", \"5 lakh students\"), what it means or how it works on the "
        "right in body (two or three short lines). Never small counts or "
        "durations (\"2 episodes\", \"8 to 12 min\"); those belong in an "
        "at-a-glance. The numbers can be the plan's own scale (marked "
        "indicative) or sourced figures with source.",
        text={"eyebrow": 40, "heading": 75, "lead": 140, "body": 300, "source": 120},
        lists={"stats": (1, 3, {"value": 12, "label": 50})},
    ),
    "innovation": LayoutRule(
        use_for="One print or digital innovation the way HT pitches it: a large "
        "mock-up of it on the left and 2 to 4 labelled parts on the right in "
        "facts, usually Idea, How it works and Why it works. For a jacket, "
        "gatefold, pull-out, flap, die-cut, masthead takeover, microsite or "
        "homepage takeover.",
        text={"eyebrow": 40, "heading": 60, "imageAlt": 140},
        lists={"facts": (2, 4, {"label": 24, "value": 200})},
        image=ImageRule(
            "12:13", "1100×1200 px",
            "Always rendered with a captioned placeholder until a real image "
            "is supplied.",
        ),
    ),
    "numbered-rows": LayoutRule(
        use_for="3 to 6 items in order, each a short title and one line: an "
        "article series, video episodes, deliverables, contest stages. The "
        "lead can carry the series' facts in one line (\"5 episodes · "
        "fortnightly · 15 to 25 min · HT YouTube\").",
        text={"eyebrow": 40, "heading": 75, "lead": 140},
        lists={"cards": (3, 6, {"title": 40, "body": 130})},
    ),
    "campaign-matrix": LayoutRule(
        use_for="The whole plan on one slide: columns are \"Channel\" then 2 to "
        "4 phases; each row a channel or component with a short line per "
        "phase (an empty string when it sits that phase out). Once, near the "
        "end.",
        text={"eyebrow": 40, "heading": 75},
        lists={"columns": (3, 5, {}), "rows": (3, 8, {})},
    ),
    "timeline": LayoutRule(
        use_for="A campaign calendar: phases or weeks in order.",
        text={"eyebrow": 40, "heading": 75},
        # Step titles must stay on one line or they run into the body below.
        lists={"steps": (3, 5, {"title": 22, "body": 170})},
    ),
    "comparison": LayoutRule(
        use_for="Two options or before/after, side by side.",
        text={"eyebrow": 40, "heading": 75, "leftLabel": 30, "rightLabel": 30,
              "left": 360, "right": 360},
    ),
    "data-table": LayoutRule(
        use_for="A deliverables grid: rows of placements by platform, format "
        "and quantity. Up to 5 columns and 7 rows. For the plan by phase use "
        "campaign-matrix.",
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

# feature-grid: the columns that fill every row for each card count. The
# first is used when the deck doesn't say. 4 cards in 3 columns leaves one
# card alone on the second row, which reads as a mistake.
_GRID_COLUMNS = {2: (2,), 3: (3,), 4: (2, 4), 6: (3, 2)}
# The widest card title that stays on one line at each column count. In a
# row where one title wraps and the others don't, the bodies misalign.
_CARD_TITLE_MAX = {2: 40, 3: 26, 4: 18}

# Position rules. Middle slides use any approved layout except these.
_FIRST, _SECOND, _LAST = "title", "two-column", "closing"
_ENDS_ONLY = {"title", "closing"}


def _placeholder(ratio: str, caption: str) -> tuple[str, str]:
    caption = (caption or "").strip() or DEFAULT_CAPTION
    return (
        f"{PLACEHOLDER_PREFIX}{ratio}",
        f"Image placeholder ({ratio}): {caption}",
    )


def _caption_of(alt: str) -> str:
    """A placeholder's description without the prefix _placeholder adds."""
    return alt.split("): ", 1)[1] if alt.startswith("Image placeholder (") else alt


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
    alt = _caption_of(obj.get("imageAlt") or "")
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
            if name == "imageAlt" and isinstance(value, str):
                # Measure the description, not the "Image placeholder (16:9): "
                # prefix this function adds itself; counting it let a deck pass
                # at build and fail every revision afterwards, naming slides
                # nobody had touched (found by the refinement evals).
                value = _caption_of(value)
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

        if layout == "feature-grid":
            # Checked here because the schema's own error for this field
            # (it also accepts "bento" and a list) is five lines, four of
            # them contradicting the real cause.
            cards = slide.get("cards") if isinstance(slide.get("cards"), list) else []
            fits = _GRID_COLUMNS.get(len(cards))
            cols = slide.get("columns", 3)
            if not isinstance(cols, int) or isinstance(cols, bool) or not 2 <= cols <= 4:
                problems.append(
                    f"{where}: columns must be the number 2, 3 or 4, not {cols!r}. "
                    "Use 2 for longer card copy, 3 for shorter."
                )
            elif fits and cols not in fits:
                # Not judgement, just arithmetic, so it's fixed rather than
                # rejected; that also mends decks built before this rule.
                slide["columns"] = fits[0]
            elif fits and "columns" not in slide:
                slide["columns"] = fits[0]

        if layout == "quote":
            # The layout draws its own curly quotes; the model often adds its
            # own. It also prefixes the attribution with an em dash, and the
            # quote slide states our idea, not a person's words, so drop it.
            if isinstance(slide.get("quote"), str):
                slide["quote"] = slide["quote"].strip().strip('"“”\'').strip()
            slide.pop("by", None)
        if layout == "two-column" and not slide.get("image") and not str(slide.get("aside") or "").strip():
            problems.append(
                f"{where}: the right half would be empty. Give it an image "
                "(\"image\": \"placeholder\" with imageAlt) or a one-line aside."
            )
        # Sources are small print at the foot (source), never slide copy.
        # A stat-row from before the field existed carried it in lead.
        lead = str(slide.get("lead") or "")
        if _SOURCE_LINE.match(lead) and not slide.get("source"):
            slide["source"] = _SOURCE_LINE.sub("", lead).strip()
            slide.pop("lead")
        if layout in ("image-hero", "innovation") and not slide.get("image"):
            slide["image"] = "placeholder"

        if rule.image:
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
    # The brief slide makes a point in words; an image there is a
    # placeholder nobody can fill meaningfully.
    if len(slides) > 2 and isinstance(slides[1], dict) and slides[1].get("layout") == "two-column":
        if slides[1].get("image") or not str(slides[1].get("aside") or "").strip():
            problems.append(
                "slide 1 (brief) must use an aside, not an image: one line "
                "that states the point of the slide."
            )
    if slides and isinstance(slides[-1], dict) and slides[-1].get("layout") != _LAST:
        problems.append("The last slide must be the closing slide (layout closing).")
    return problems


def _is_module_slide(slide) -> bool:
    return isinstance(slide, dict) and str(slide.get("notes", "")).startswith("[why-ht:")


def first_draft_problems(deck: dict) -> list[str]:
    """Rules only a newly built deck must meet, on top of enforce().

    Kept out of enforce() because enforce() also guards every revision, and
    a deck built before a rule existed must stay editable.
    """
    problems = []
    for i, slide in enumerate(deck.get("slides") or []):
        if not isinstance(slide, dict) or slide.get("layout") != "feature-grid":
            continue
        cards = slide.get("cards") if isinstance(slide.get("cards"), list) else []
        if cards and len(cards) not in _GRID_COLUMNS:
            problems.append(
                f"slide {i} (feature-grid): {len(cards)} cards can't fill every "
                "row. Use 2, 3, 4 or 6 cards, or split them across two slides."
            )
            continue
        labelled = [j for j, c in enumerate(cards) if isinstance(c, dict) and _LABEL_LINE.search(str(c.get("body") or ""))]
        if labelled:
            problems.append(
                f"slide {i} (feature-grid): cards {', '.join(map(str, labelled))} use "
                "\"**Label:** value\" lines. A card's text is a sentence or two, or "
                "plain bullets; put labelled facts on an at-a-glance slide."
            )
        cols = slide.get("columns")
        limit = _CARD_TITLE_MAX.get(cols, 40) if isinstance(cols, int) else 40
        for j, card in enumerate(cards):
            title = card.get("title") if isinstance(card, dict) else None
            if isinstance(title, str) and len(title) > limit:
                problems.append(
                    f"slide {i} (feature-grid): cards[{j}].title is {len(title)} "
                    f"characters; in {cols} columns a title must fit one line, "
                    f"so the limit is {limit}. Shorten it."
                )

    problems += _readability_problems(deck) + _component_problems(deck)
    for i, slide in enumerate(deck.get("slides") or []):
        if not isinstance(slide, dict) or _is_module_slide(slide):
            continue
        if slide.get("layout") == "stat-row" and not str(slide.get("source") or "").strip():
            problems.append(
                f"slide {i} (stat-row): give the figures' source in source, e.g. "
                "\"IRS 2019\". Use another layout if the numbers have none."
            )
        cited = [name for name, text in _copy(slide) if _CITATION.search(text)]
        if _PAST_WORK.search(str(slide.get("source") or "")):
            cited.append("source")
        if cited:
            problems.append(
                f"slide {i} ({slide.get('layout')}): {', '.join(cited)} talk about "
                "sources. Slide copy never names a source or a past deck; a real "
                "figure's source goes in source, shown as small print."
            )

    pictured = sum(
        1 for s in deck.get("slides") or []
        if isinstance(s, dict) and not _is_module_slide(s) and s.get("image")
        and s.get("layout") in _PICTURE_LAYOUTS
    )
    fewest, most = picture_range(deck)
    if pictured < fewest:
        problems.append(
            f"The deck has {pictured} slides with a picture; this one needs at "
            f"least {fewest} (one for every three content slides). Give the big "
            "idea an image-hero, each component's what-it-is slide a picture, "
            "and at-a-glance slides a picture of the component (\"image\": "
            "\"placeholder\" with a description in imageAlt). The brief slide "
            "keeps its aside."
        )
    elif pictured > most:
        problems.append(
            f"The deck has {pictured} slides with a picture; at most {most}. "
            "Keep pictures for the big idea and the components, and drop the rest."
        )
    return problems


_PICTURE_LAYOUTS = ("two-column", "image-hero", "at-a-glance", "innovation")
_NOT_CONTENT = ("title", "section", "closing")


def picture_range(deck: dict) -> tuple[int, int]:
    """(fewest, most) pictures a first draft may have: one for every three
    content slides at least, one for every two at most, never past MAX_IMAGES."""
    content = sum(
        1 for s in deck.get("slides") or []
        if isinstance(s, dict) and not _is_module_slide(s) and s.get("layout") not in _NOT_CONTENT
    )
    most = min(content // 2, MAX_IMAGES)
    return min(-(-content // 3), most), most


# A line longer than this reads as a paragraph on a slide: split it into points.
MAX_LINE_WORDS = 35
_PROSE_FIELDS = ("body", "lead", "left", "right")


def _prose(slide: dict):
    """(field name, text) for every block of body copy on a slide."""
    for name in _PROSE_FIELDS:
        if isinstance(slide.get(name), str):
            yield name, slide[name]
    for key, item_field in (("cards", "body"), ("steps", "body"), ("facts", "value")):
        for j, item in enumerate(slide.get(key) or []):
            if isinstance(item, dict) and isinstance(item.get(item_field), str):
                yield f"{key}[{j}].{item_field}", item[item_field]


# Highlights mark what a reader must not miss; more than this and none stands out.
MAX_HIGHLIGHTS = 2
_HIGHLIGHT = re.compile(r"==[^=]+==")
# A line that opens with a bold label: "**Reach:** 2,000 schools".
_LABEL_LINE = re.compile(r"^\s*(?:[-\u2022]\s*)?\*\*[^*\n]{1,40}(?::\*\*|\*\*\s*:)", re.M)
_SOURCE_LINE = re.compile(r"^\s*sources?\s*:\s*", re.I)
# Slide copy that talks about where something came from.
_CITATION = re.compile(r"\bsources?\s*:|\baccording to\b|\bpast (pitch )?decks?\b|\bprior HT work\b", re.I)
# A source naming HT's own earlier pitches: those are proposals, not evidence.
_PAST_WORK = re.compile(r"\b(proposals?|decks?|pitch(es)?)\b", re.I)


def _copy(slide: dict):
    """(field, text) for every piece of copy a reader sees, source excepted."""
    for name in ("heading", "lead", "aside", "quote"):
        if isinstance(slide.get(name), str):
            yield name, slide[name]
    yield from _prose(slide)
    for j, card in enumerate(slide.get("cards") or []):
        if isinstance(card, dict) and isinstance(card.get("title"), str):
            yield f"cards[{j}].title", card["title"]


def _readability_problems(deck: dict) -> list[str]:
    problems = []
    slides = [s for s in deck.get("slides") or [] if isinstance(s, dict) and not _is_module_slide(s)]
    for i, slide in enumerate(deck.get("slides") or []):
        if not isinstance(slide, dict):
            continue
        for name, text in _prose(slide):
            longest = max((len(line.split()) for line in text.split("\n")), default=0)
            if longest > MAX_LINE_WORDS:
                problems.append(
                    f"slide {i} ({slide.get('layout')}): {name} has a {longest}-word "
                    f"paragraph; the limit is {MAX_LINE_WORDS} words per line. Break it "
                    "into short points (\"- \" lines) or two short sentences on "
                    "their own lines."
                )
        marks = sum(len(_HIGHLIGHT.findall(text)) for _, text in _prose(slide))
        marks += len(_HIGHLIGHT.findall(str(slide.get("aside") or "")))
        if marks > MAX_HIGHLIGHTS:
            problems.append(
                f"slide {i} ({slide.get('layout')}): {marks} ==highlights==; at most "
                f"{MAX_HIGHLIGHTS} a slide. Keep them for a figure, a name or place, "
                "or the one idea the client must remember."
            )
    two_column = sum(1 for s in slides if s.get("layout") == "two-column")
    allowed = max(4, len(slides) // 3)
    if two_column > allowed:
        problems.append(
            f"{two_column} of {len(slides)} slides are two-column; at most {allowed}. "
            "Use at-a-glance, options, feature-grid, timeline, comparison or "
            "data-table where they fit the content better."
        )
    return problems


# What it is, how it works, its facts: the least a component needs.
MIN_COMPONENT_SLIDES = 3


def _component_problems(deck: dict) -> list[str]:
    """Each component on the solution overview needs depth: at least two
    slides of its own, one of them an at-a-glance with its facts."""
    slides = deck.get("slides") or []
    overview = next((s for s in slides[2:] if isinstance(s, dict) and s.get("layout") == "feature-grid"), None)
    if overview is None:
        return []
    problems = []
    shapes: dict[tuple, list[str]] = {}
    for card in overview.get("cards") or []:
        name = card.get("title") if isinstance(card, dict) else None
        if not name:
            continue
        own = [s for s in slides if isinstance(s, dict) and s is not overview and s.get("eyebrow") == name]
        if len(own) < MIN_COMPONENT_SLIDES:
            problems.append(
                f'Component "{name}" has {len(own)} slide(s) with that eyebrow; each '
                f"component on the overview needs at least {MIN_COMPONENT_SLIDES}: what it "
                "is, how it works (its mechanics, samples, episodes or options), and its "
                "facts (at-a-glance)."
            )
        elif not any(s.get("layout") == "at-a-glance" for s in own):
            problems.append(
                f'Component "{name}" has no at-a-glance slide: add one with its '
                "platform, format, frequency, duration, geography and how it is measured."
            )
        if own:
            shapes.setdefault(tuple(s.get("layout") for s in own), []).append(name)
        if any(s.get("htIp") for s in own) and not all(s.get("htIp") for s in own):
            problems.append(
                f'Component "{name}" is an HT IP on some of its slides but not all: '
                'set "htIp": true on every slide of it.'
            )
    # Every component told in the same run of layouts is what makes a deck
    # read as one slide repeated; at most half may share one.
    same = max(shapes.values(), key=len, default=[])
    if len(shapes) and len(same) >= 3 and len(same) * 2 > sum(map(len, shapes.values())):
        problems.append(
            f"Components {', '.join(same)} all use the same slides "
            f"({' then '.join(next(k for k, v in shapes.items() if v is same))}). "
            "Show each in the layout that fits it: innovation for a print or "
            "digital innovation, numbered-rows for a series, stat-story for a "
            "programme whose scale is the point, two-column for an event."
        )
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
        "the insight, the big idea (quote or image-hero), an overview of the "
        "solution's components (feature-grid), then each component in turn, the "
        "plan (timeline, campaign-matrix or data-table), and why this works "
        "(a feature-grid of 3 reasons it suits this client).",
        "  last. closing: one line to end on, like \"Let's build this together\".",
        "There is no next-steps or commercials slide, and no slide about past "
        "work for other clients: HT's own decks have neither. Never mention "
        "prices or costing anywhere; HT's sales team handles them.",
        "",
        "Every slide about one of HT's own properties or IPs (HT PACE, Fresh on "
        "Campus, Anokhee Club, Hindustan Olympiad, Weekend Sorted, an HT or Mint "
        "summit) sets \"htIp\": true, which shows an HT MEDIA IP badge, and the "
        "IP's card on the overview says it is HT's own.",
        "",
        "Never use an em dash (—) or a spaced en dash ( – ) anywhere in a deck. "
        "They read as machine-written. Use a comma, colon or full stop.",
        "",
        "Choose how to present each slide's text by what it is, and vary it "
        "across the deck:",
        "  - an overview, context or the idea itself: a short paragraph, one or "
        "two plain sentences, each on its own line;",
        "  - a list of similar things: plain bullets (lines starting \"- \");",
        "  - a list of named things (activities, formats, touchpoints, each with "
        "its own name): \"**Name:** what it is\" lines, e.g. \"**Classroom Called "
        "Nature:** a slip contest run before the panels\"; never inside "
        "feature-grid cards, whose text is a sentence or two or plain bullets;",
        "  - attributes of one thing (platform, timing, reach): an at-a-glance "
        "slide;",
        "  - an innovation (a jacket, a gatefold, a takeover): innovation, with "
        "Idea, How it works and Why it works;",
        "  - a series of articles or episodes, deliverables in order: "
        "numbered-rows; a scale worth showing big: stat-story; the whole plan "
        "by phase: campaign-matrix; steps over time: timeline; choices: "
        "options; real figures: stat-row; two things contrasted: comparison; "
        "one big idea: quote or image-hero.",
        "Sources never appear in slide copy, and slide copy never mentions past "
        "decks or HT's earlier pitches. A real figure from research keeps its "
        "source in the slide's source field, which shows as small print at the "
        "foot, the way HT's decks cite IRS or Comscore. ==text== is "
        "highlighted in HT's accent: use it only on a figure, a name or place, "
        "or the one idea the client must remember, never on a general phrase, "
        f"and at most {MAX_HIGHLIGHTS} a slide. **text** is bold. A line over "
        f"{MAX_LINE_WORDS} words is rejected: keep paragraphs short.",
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
        "Images: only two-column (the right half, 12:13), at-a-glance (beside "
        "the facts, 12:13), innovation (the mock-up, 12:13) and image-hero "
        "(full slide, 16:9) take one, plus logo-wall cards for logos. Never put an "
        "image inside a feature-grid card; give it its own slide. Set "
        "\"image\": \"placeholder\" and describe the picture in imageAlt: "
        "an image is generated from that description when the deck is built. "
        "Describe a concrete scene in an Indian setting (who, where, what is "
        "happening, the mood), for example \"Homemakers watching a street "
        "theatre troupe perform at a busy weekly haat in a small UP town\". "
        "Generic scenes and mock-ups (a sample newspaper page, a branded "
        "canopy) are fine, and a mock-up of the component itself (the HT City "
        "page carrying the feature, the article page, the event stage) is often "
        "the best picture. A first draft needs one picture for every three "
        "content slides (not counting title, section and closing) and at most "
        f"one for every two, never more than {MAX_IMAGES}: give the big idea an "
        "image-hero and each component a picture on its what-it-is or "
        "at-a-glance slide. Never write an image URL.",
    ]
    return "\n".join(lines)
