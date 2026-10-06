"""The HT master deck rules and the Why HT module, pinned to what matters.

Each test names a way a deck could reach a client looking wrong: an overfull
slide, a broken image frame, the wrong theme, invented credentials, or a
credentials section duplicated by a second request.
"""

from __future__ import annotations

import copy
import json
from unittest.mock import patch

import pytest

from agents.solutioning_agent.tools import deck as deck_tools
from agents.solutioning_agent.tools import master_deck, why_ht


def _component_slides(name, detail="options"):
    """The depth a first draft needs for each component on the overview."""
    if detail == "numbered-rows":
        return _component_slides(name)[::2] + [
            {"layout": "numbered-rows", "eyebrow": name, "heading": f"The {name} series",
             "cards": [{"title": "One", "body": "First"}, {"title": "Two", "body": "Second"},
                       {"title": "Three", "body": "Third"}]}]
    return [
        {"layout": "at-a-glance", "eyebrow": name, "heading": f"{name} at a glance",
         "image": "placeholder", "imageAlt": f"A mock-up of the {name} feature",
         "facts": [{"label": "Platform", "value": "HT"}, {"label": "Format", "value": "Page"},
                   {"label": "Frequency", "value": "Weekly"}, {"label": "Geography", "value": "Delhi NCR"}]},
        {"layout": "options", "eyebrow": name, "heading": f"Two ways to run {name}",
         "cards": [{"title": "Option one", "body": "- Fast"}, {"title": "Option two", "body": "- Wide"}]},
        {"layout": "comparison", "eyebrow": name, "heading": f"How {name} works",
         "leftLabel": "Today", "rightLabel": "With HT", "left": "- Ads", "right": "- Moments"},
    ]


def _deck(**overrides):
    deck = {
        "type": "deck",
        "meta": {"title": "Acme × HT"},
        "slides": [
            {"layout": "title", "eyebrow": "HT Media × Acme", "heading": "Acme festive"},
            {"layout": "two-column", "heading": "The brief", "body": "Drive trial.", "aside": "In 8 weeks"},
            {"layout": "quote", "quote": "Make the sale an event."},
            {"layout": "feature-grid", "columns": 3,
             "cards": [{"title": "Print"}, {"title": "Digital"}, {"title": "On-ground"}]},
            {"layout": "timeline", "steps": [{"title": "Tease"}, {"title": "Launch"}, {"title": "Sustain"}]},
            {"layout": "image-hero", "heading": "The idea", "image": "placeholder", "imageAlt": "A festive market"},
            {"layout": "two-column", "heading": "Pillar 1", "body": "Canopies.",
             "image": "placeholder", "imageAlt": "A canopy"},
            {"layout": "two-column", "heading": "Pillar 2", "body": "Kiranas.",
             "image": "placeholder", "imageAlt": "A kirana counter"},
            *_component_slides("Print"), *_component_slides("Digital"), *_component_slides("On-ground", "numbered-rows"),
            {"layout": "two-column", "heading": "Next steps", "body": "Costing from HT's pricing team.",
             "aside": "Commercials from HT's pricing team."},
            {"layout": "closing", "heading": "Thank you"},
        ],
    }
    deck.update(overrides)
    return deck


def test_a_deck_on_the_spine_passes_and_is_forced_onto_the_ht_theme():
    deck = _deck(meta={"title": "x", "theme": "candy-pop"})
    assert master_deck.enforce(deck) == []
    assert deck["meta"]["theme"] == "ht-media"


def test_overlong_text_is_rejected_with_the_field_named_never_truncated():
    deck = _deck()
    long_body = "word " * 150
    deck["slides"][1]["body"] = long_body
    problems = master_deck.enforce(deck)
    assert any("slide 1 (two-column): body is 750 characters; the limit is 600" in p for p in problems)
    assert deck["slides"][1]["body"] == long_body


def test_list_items_are_bounded_in_count_and_length():
    deck = _deck()
    deck["slides"][3]["cards"] = [{"title": f"Card {i}"} for i in range(7)]
    deck["slides"][4]["steps"][0]["body"] = "x" * 171
    problems = master_deck.enforce(deck)
    assert any("cards has 7; it must have 2–6" in p for p in problems)
    assert any("steps[0].body is 171 characters" in p for p in problems)


@pytest.mark.parametrize("layout", ["chart", "custom-html", "code", "metric-ring", "ranked-list"])
def test_layouts_outside_the_approved_set_are_rejected(layout):
    deck = _deck()
    deck["slides"][2] = {"layout": layout, "heading": "x"}
    assert any("layout is not approved" in p for p in master_deck.enforce(deck))


def test_the_spine_is_enforced_at_both_ends():
    deck = _deck()
    deck["slides"][0], deck["slides"][2] = deck["slides"][2], deck["slides"][0]
    deck["slides"][-1] = {"layout": "section", "heading": "End"}
    problems = master_deck.enforce(deck)
    assert "slide 0 must be the title slide (layout title)." in problems
    assert "The last slide must be the closing slide (layout closing)." in problems
    assert any("title may only be the first or last slide" in p for p in problems)


def test_slide_count_is_bounded():
    deck = _deck()
    deck["slides"] = deck["slides"][:2] + deck["slides"][-1:]
    assert any("must have 7–40" in p for p in master_deck.enforce(deck))


def test_forty_own_slides_pass_and_forty_one_do_not():
    deck = _deck()
    filler = {"layout": "quote", "quote": "One more idea."}
    deck["slides"] = deck["slides"][:-2] + [dict(filler) for _ in range(40 - len(deck["slides"]))] + deck["slides"][-2:]
    assert len(deck["slides"]) == 40
    assert master_deck.enforce(deck) == []
    deck["slides"].insert(3, dict(filler))
    assert any("must have 7–40" in p for p in master_deck.enforce(deck))


def test_an_image_the_model_cannot_supply_becomes_a_captioned_placeholder():
    deck = _deck()
    deck["slides"][2] = {"layout": "two-column", "heading": "Insight", "body": "x",
                         "image": "https://example.com/made-up.jpg",
                         "imageAlt": "Homemaker cooking with the sachet"}
    deck["slides"][4] = {"layout": "image-hero", "heading": "The big idea"}
    assert master_deck.enforce(deck) == []
    assert deck["slides"][2]["image"] == "placeholder://12:13"
    assert deck["slides"][2]["imageAlt"] == "Image placeholder (12:13): Homemaker cooking with the sachet"
    # image-hero always carries a visual, so a missing one is a placeholder too
    assert deck["slides"][4]["image"] == "placeholder://16:9"


def test_placeholder_captions_do_not_stack_on_re_enforcement():
    deck = _deck()
    deck["slides"][2] = {"layout": "two-column", "heading": "Insight", "image": "placeholder", "imageAlt": "Kirana counter"}
    master_deck.enforce(deck)
    master_deck.enforce(deck)
    assert deck["slides"][2]["imageAlt"] == "Image placeholder (12:13): Kirana counter"


def test_the_brief_slide_makes_its_point_in_an_aside_not_an_image():
    deck = _deck()
    deck["slides"][1].update(image="placeholder", imageAlt="Calendar")
    deck["slides"][1].pop("aside", None)
    assert any("slide 1 (brief) must use an aside" in p for p in master_deck.enforce(deck))


def test_the_slide_before_closing_may_take_a_picture_now_there_is_no_next_steps_slide():
    deck = _deck()
    nxt = len(deck["slides"]) - 2
    deck["slides"][nxt].update(image="placeholder", imageAlt="Families at a campsite")
    deck["slides"][nxt].pop("aside", None)
    assert master_deck.enforce(deck) == []


@pytest.mark.parametrize("cols", [5, 1, "3", "bento", ["a", "b"], True])
def test_feature_grid_columns_get_one_clear_message(cols):
    deck = _deck()
    deck["slides"][3]["columns"] = cols
    problems = [p for p in master_deck.enforce(deck) if "columns" in p]
    assert len(problems) == 1
    assert "columns must be the number 2, 3 or 4" in problems[0]


def test_feature_grid_cards_never_carry_images():
    deck = _deck()
    deck["slides"][3]["cards"][0]["image"] = "placeholder"
    assert any("Cards are text only" in p for p in master_deck.enforce(deck))


def test_em_dashes_and_spaced_en_dashes_are_rejected_but_ranges_are_fine():
    deck = _deck()
    deck["slides"][4]["steps"][0]["body"] = "Weeks 1–6"
    assert master_deck.enforce(deck) == []
    deck["slides"][1]["body"] = "Drive trial — fast."
    deck["slides"][3]["cards"][1]["body"] = "Print – then digital"
    problems = master_deck.enforce(deck)
    assert any("slide 1 (two-column): body use an em dash" in p for p in problems)
    assert any("cards[1].body" in p for p in problems)


def test_quote_slides_drop_the_attribution_the_renderer_prefixes_with_a_dash():
    deck = _deck()
    deck["slides"][2]["by"] = "Campaign big idea"
    master_deck.enforce(deck)
    assert "by" not in deck["slides"][2]


def test_a_real_embedded_image_is_left_alone():
    deck = _deck()
    deck["slides"][2] = {"layout": "two-column", "heading": "Insight", "image": "data:image/png;base64,iVBORw0KGgo="}
    master_deck.enforce(deck)
    assert deck["slides"][2]["image"] == "data:image/png;base64,iVBORw0KGgo="


def test_layouts_without_an_image_slot_reject_one():
    deck = _deck()
    deck["slides"][2]["image"] = "placeholder"
    assert any("does not take an image" in p for p in master_deck.enforce(deck))


def test_quote_marks_the_layout_draws_itself_are_stripped():
    deck = _deck()
    deck["slides"][2]["quote"] = '"Asli swad, ab ₹10 mein"'
    master_deck.enforce(deck)
    assert deck["slides"][2]["quote"] == "Asli swad, ab ₹10 mein"


def test_a_stat_row_needs_its_source_as_small_print():
    deck = _deck()
    deck["slides"][2] = {"layout": "stat-row", "heading": "Why now",
                         "stats": [{"value": "38%", "label": "Spend more on beauty"}, {"value": "70%", "label": "Shop in store"}]}
    assert any("give the figures' source in source" in p for p in master_deck.first_draft_problems(deck))
    deck["slides"][2]["source"] = "LocalCircles survey 2025"
    assert not any("source" in p for p in master_deck.first_draft_problems(deck))


def test_a_source_written_in_the_lead_moves_to_the_small_print():
    """Decks built before the source field carried it in lead; they stay editable."""
    deck = _deck()
    deck["slides"][2] = {"layout": "stat-row", "heading": "Why now", "lead": "Source: IRS 2019.",
                         "stats": [{"value": "8.6M", "label": "Readers"}, {"value": "#1", "label": "In Delhi"}]}
    assert master_deck.enforce(deck) == []
    assert deck["slides"][2]["source"] == "IRS 2019." and "lead" not in deck["slides"][2]


@pytest.mark.parametrize("text", [
    "Source: HT PACE deck 2025",
    "According to the Lavie proposal, 20 colleges took part.",
    "Built on prior HT work for Sensodyne.",
    "As our past decks show, schools respond.",
])
def test_slide_copy_never_talks_about_sources_or_past_decks(text):
    deck = _deck()
    deck["slides"][6]["body"] = text
    assert any("talk about sources" in p for p in master_deck.first_draft_problems(deck))


def test_the_agent_instruction_is_generated_from_the_enforced_limits():
    text = master_deck.describe_for_agent()
    for name, rule in master_deck.LAYOUTS.items():
        assert f"  {name}: " in text
        for field, limit in rule.text.items():
            assert f"{field} ≤{limit}" in text


# --- Why HT -------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _no_drive_logo():
    with patch.object(why_ht.visuals, "ht_logo", return_value=None):
        yield


def test_why_ht_always_adds_opener_and_scale_and_at_most_two_markets():
    slides, unknown = why_ht.slides_for(["digital", "english-print", "delhi-ncr"])
    tags = [s["notes"].split("]")[0] for s in slides]
    assert tags == ["[why-ht:opener", "[why-ht:scale", "[why-ht:digital", "[why-ht:english-print"]
    assert unknown == []


def test_why_ht_figures_are_the_fixed_library_not_generated():
    slides, _ = why_ht.slides_for(["hindi-heartland"])
    heartland = slides[-1]
    assert [s["value"] for s in heartland["stats"]] == ["50 MN", "#1", "#1", "4X"]
    assert heartland["source"].startswith("IRS 2019") and "lead" not in heartland


def test_why_ht_goes_straight_after_the_brief_and_replaces_itself():
    deck = _deck()
    why_ht.insert(deck, ["english-print"])
    why_ht.insert(deck, ["hindi-heartland"])
    module = [s for s in deck["slides"] if why_ht.is_module_slide(s)]
    assert len(module) == 3  # replaced, not stacked
    assert module[-1]["heading"] == "The Hindi heartland's trusted daily"
    # where HT's own decks put their credentials: right after the brief
    assert [why_ht.is_module_slide(s) for s in deck["slides"][:5]] == [False, False, True, True, True]
    assert deck["slides"][1]["heading"] == "The brief"
    assert deck["slides"][-2]["heading"] == "Next steps"


def test_why_ht_content_has_no_em_dashes():
    slides, _ = why_ht.slides_for(list(why_ht.VARIANTS))
    assert not any("—" in json.dumps(s, ensure_ascii=False) for s in slides)


def test_every_why_ht_slide_passes_the_master_deck_rules():
    deck = _deck()
    why_ht.insert(deck, ["english-print", "delhi-ncr"])
    assert master_deck.enforce(deck) == []
    why_ht.insert(deck, ["hindi-heartland", "digital"])
    # the module doesn't count against the slide limit either
    assert master_deck.enforce(deck) == []


def test_without_the_asset_folder_the_opener_shows_a_logo_placeholder():
    deck = _deck()
    why_ht.insert(deck, [])
    master_deck.enforce(deck)
    opener = next(s for s in deck["slides"] if s.get("notes", "").startswith("[why-ht:opener"))
    assert opener["image"] == "placeholder://12:13"
    assert opener["imageFit"] == "contain"


def test_add_why_ht_slides_republishes_in_place():
    stored = {
        "brief_id": "b1", "client_name": "Acme", "deck_file_id": "file-123",
        "deck_link": "https://docs.google.com/presentation/d/file-123/edit",
        "deck_json": json.dumps(_deck()),
    }
    saved = {}
    with patch.object(deck_tools, "_load_brief", return_value=stored), \
         patch.object(deck_tools, "_render_pptx", return_value=b"pptx"), \
         patch.object(deck_tools, "_upload_pptx") as upload, \
         patch.object(deck_tools, "_save_brief", side_effect=lambda bid, **kw: saved.update(kw)):
        result = deck_tools.add_why_ht_slides("b1", "english-print")

    assert result["why_ht_slides"] == ["opener", "scale", "english-print"]
    assert upload.call_args.kwargs["file_id"] == "file-123"
    assert len(json.loads(saved["deck_json"])["slides"]) == len(_deck()["slides"]) + 3


def test_an_unknown_variant_changes_nothing():
    stored = {"brief_id": "b1", "deck_file_id": "f", "deck_link": "l", "deck_json": json.dumps(_deck())}
    with patch.object(deck_tools, "_load_brief", return_value=stored), \
         patch.object(deck_tools, "_upload_pptx") as upload:
        result = deck_tools.add_why_ht_slides("b1", "tamil-nadu")
    assert "Unknown variants ['tamil-nadu']" in result["error"]
    upload.assert_not_called()


def test_build_rejects_a_deck_off_the_spine_before_rendering():
    deck = _deck()
    deck["slides"] = deck["slides"][1:]
    with patch.object(deck_tools, "_render_pptx") as render:
        result = deck_tools.build_solution_deck(json.dumps(deck), "Acme", "b1")
    assert result["problems"]
    render.assert_not_called()



# --- feature-grid shape and first-draft rules -----------------------------------


def _grid(n, columns=None, title="Card"):
    slide = {"layout": "feature-grid", "heading": "Pillars",
             "cards": [{"title": f"{title} {i}"} for i in range(n)]}
    if columns is not None:
        slide["columns"] = columns
    return slide


@pytest.mark.parametrize("n, given, expected", [
    (4, None, 2), (4, 3, 2), (4, 4, 4), (3, None, 3), (3, 2, 3),
    (6, None, 3), (6, 4, 3), (6, 2, 2), (2, 3, 2),
])
def test_grid_columns_are_set_so_every_row_is_full(n, given, expected):
    """The Rapido deck's 4 pillars in the default 3 columns left one card alone."""
    deck = _deck()
    deck["slides"][3] = _grid(n, given)
    assert master_deck.enforce(deck) == []
    assert deck["slides"][3]["columns"] == expected


def test_five_cards_are_rejected_on_a_first_draft_but_never_block_a_revision():
    deck = _deck()
    deck["slides"][3] = _grid(5)
    assert master_deck.enforce(deck) == []
    assert any("5 cards can't fill every row" in p for p in master_deck.first_draft_problems(deck))


def test_a_card_title_that_would_wrap_in_three_columns_is_rejected_on_a_first_draft():
    deck = _deck()
    deck["slides"][3] = _grid(3, 3, title="Interactive Commute Dashb")  # 27 chars with the index
    master_deck.enforce(deck)
    problems = master_deck.first_draft_problems(deck)
    assert any("title must fit one line" in p and "limit is 26" in p for p in problems)
    deck["slides"][3] = _grid(4, 2, title="Interactive Commute Dashboard")
    master_deck.enforce(deck)
    assert not any("title must fit one line" in p for p in master_deck.first_draft_problems(deck))


def test_pictures_scale_with_the_deck_one_per_three_to_one_per_two_content_slides():
    deck = _deck()  # 17 content slides: title and closing don't count
    assert master_deck.picture_range(deck) == (6, 8)
    deck["slides"] = deck["slides"] * 3  # 42 content slides, but never past the cap
    assert master_deck.picture_range(deck) == (master_deck.MAX_IMAGES, master_deck.MAX_IMAGES)


def test_a_first_draft_needs_a_picture_for_every_three_content_slides():
    deck = _deck()
    master_deck.enforce(deck)
    assert master_deck.first_draft_problems(deck) == []
    for i in (7, 6):
        deck["slides"][i].pop("image")
        deck["slides"][i]["aside"] = "Kiranas."
    problems = master_deck.first_draft_problems(deck)
    assert any("4 slides with a picture" in p and "at least 6" in p for p in problems)


def test_a_first_draft_may_not_picture_more_than_every_other_slide():
    deck = _deck()
    for i in (1, 17):
        deck["slides"][i]["image"] = "placeholder"
        deck["slides"][i]["imageAlt"] = "A crowded market"
    deck["slides"][2] = {"layout": "image-hero", "heading": "The idea", "imageAlt": "A crowded market"}
    master_deck.enforce(deck)
    assert any("9 slides with a picture; at most 8" in p for p in master_deck.first_draft_problems(deck))


def test_why_ht_logo_slots_do_not_count_toward_the_image_minimum():
    deck = _deck()
    for i in (7, 6):
        deck["slides"][i].pop("image")
        deck["slides"][i]["aside"] = "x"
    why_ht.insert(deck, [])
    master_deck.enforce(deck)
    assert any("4 slides with a picture" in p for p in master_deck.first_draft_problems(deck))


def test_build_rejects_a_first_draft_short_of_images_before_rendering():
    deck = _deck()
    deck["slides"] = [s for s in deck["slides"] if s.get("layout") != "image-hero"]
    deck["slides"][6].pop("image")
    deck["slides"][6]["aside"] = "x"
    with patch.object(deck_tools, "_render_pptx") as render:
        result = deck_tools.build_solution_deck(json.dumps(deck), "Acme", "b1")
    render.assert_not_called()
    assert any("at least 6" in p for p in result["problems"])


def test_at_most_two_highlights_a_slide():
    deck = _deck()
    deck["slides"][17]["body"] = "- ==One== and ==two==\n- and ==three=="
    problems = master_deck.first_draft_problems(deck)
    assert any("slide 17" in p and "3 ==highlights==" in p for p in problems)


def test_named_lists_may_use_labels_on_any_slide():
    deck = _deck()
    for i in (1, 6, 7, 17):
        deck["slides"][i]["body"] = "- **Reach:** Delhi\n- **Format:** Page\n- **When:** Diwali"
    assert master_deck.first_draft_problems(deck) == []


@pytest.mark.parametrize("slide", [
    {"layout": "innovation", "eyebrow": "Print", "heading": "The smooth switch",
     "imageAlt": "A flap jacket on HT's front page",
     "facts": [{"label": "Idea", "value": "A flap reveals the bike."}, {"label": "How it works", "value": "Lift the flap."},
               {"label": "Why it works", "value": "Readers touch it."}]},
    {"layout": "numbered-rows", "heading": "Off the Field", "lead": "5 episodes · fortnightly · HT YouTube",
     "cards": [{"title": "The gully that made me", "body": "Childhood memory"},
               {"title": "The family stand", "body": "The people behind the player"},
               {"title": "Beyond cricket", "body": "Life past the boundary"}]},
    {"layout": "stat-story", "heading": "Gully cricket meets the big league",
     "stats": [{"value": "100-120", "label": "RWAs across Delhi (indicative)"}],
     "body": "A city-wide community league with zonal finals."},
    {"layout": "campaign-matrix", "heading": "The plan at a glance",
     "columns": ["Channel", "Weeks 1-6", "Weeks 7-11"],
     "rows": [["Print", "Launch spread", "Finale wrap"], ["Radio", "Season promos", ""], ["On-ground", "", "Mall zones"]]},
])
def test_the_new_layouts_are_approved_and_checked(slide):
    deck = _deck()
    deck["slides"][7] = slide
    assert master_deck.enforce(deck) == []
    if slide["layout"] == "innovation":
        assert deck["slides"][7]["image"].startswith(master_deck.PLACEHOLDER_PREFIX)


def test_a_revision_of_a_deck_with_no_images_is_still_allowed():
    """Decks built before the image minimum must stay editable."""
    old = _deck()
    old["slides"] = [s for s in old["slides"] if not s.get("image")]
    stored = {"deck_json": json.dumps(old), "deck_file_id": "f1", "deck_link": "L", "client_name": "Acme"}
    with patch.object(deck_tools, "_load_brief", return_value=stored), \
         patch.object(deck_tools, "_render_pptx", return_value=b"pptx"), \
         patch.object(deck_tools, "_upload_pptx"), \
         patch.object(deck_tools, "_save_brief"):
        result = deck_tools.update_deck("b1", json.dumps([{"slide_index": 2, "field": "quote", "value": "New."}]))
    assert result.get("edits_applied") == ["slide 2: quote"]



def test_a_placeholder_caption_at_the_limit_still_passes_on_every_later_check():
    """The prefix enforce adds must not count: a deck that passed at build must
    pass on revision too, or every edit fails naming slides nobody touched."""
    deck = _deck()
    caption = "x" * 140   # exactly two-column's imageAlt limit
    deck["slides"][6]["imageAlt"] = caption
    assert master_deck.enforce(deck) == []
    assert deck["slides"][6]["imageAlt"].startswith("Image placeholder (12:13): ")
    assert master_deck.enforce(json.loads(json.dumps(deck))) == []


def test_a_caption_over_the_limit_is_still_rejected():
    deck = _deck()
    deck["slides"][6]["imageAlt"] = "x" * 141
    assert any("imageAlt is 141 characters" in p for p in master_deck.enforce(deck))


def test_a_first_draft_rejects_a_paragraph_but_accepts_the_same_words_as_points():
    deck = _deck()
    deck["slides"][6]["body"] = " ".join(["word"] * 40)
    master_deck.enforce(deck)
    assert any("40-word paragraph" in p for p in master_deck.first_draft_problems(deck))
    deck["slides"][6]["body"] = "\n".join("- " + " ".join(["word"] * 10) for _ in range(4))
    assert not any("paragraph" in p for p in master_deck.first_draft_problems(deck))


def test_a_first_draft_cannot_lean_on_two_column():
    deck = _deck()
    extra = {"layout": "two-column", "heading": "More", "body": "- x", "aside": "y"}
    deck["slides"][-2:-2] = [dict(extra) for _ in range(4)]
    master_deck.enforce(deck)
    assert any("are two-column; at most" in p for p in master_deck.first_draft_problems(deck))


def test_each_component_needs_three_slides_including_an_at_a_glance():
    deck = _deck()
    deck["slides"] = [s for s in deck["slides"] if not (s.get("eyebrow") == "Digital" and s["layout"] == "options")]
    deck["slides"] = [s for s in deck["slides"] if not (s.get("eyebrow") == "Print" and s["layout"] == "at-a-glance")]
    deck["slides"].insert(-2, {"layout": "comparison", "eyebrow": "Print", "heading": "x", "left": "a", "right": "b"})
    master_deck.enforce(deck)
    problems = master_deck.first_draft_problems(deck)
    assert any('"Digital" has 2 slide' in p for p in problems)
    assert any('"Print" has no at-a-glance' in p for p in problems)
    assert not any('"On-ground"' in p for p in problems)


def test_a_two_column_slide_never_leaves_its_right_half_empty():
    deck = _deck()
    deck["slides"][6].pop("image")
    assert any("right half would be empty" in p for p in master_deck.enforce(deck))


def test_components_may_not_all_be_told_in_the_same_slides():
    deck = _deck()
    assert not any("all use the same slides" in p for p in master_deck.first_draft_problems(deck))
    deck["slides"] = [s for s in deck["slides"] if s.get("eyebrow") != "On-ground" or s["layout"] == "at-a-glance"]
    at = next(i for i, s in enumerate(deck["slides"]) if s.get("eyebrow") == "On-ground") + 1
    deck["slides"][at:at] = _component_slides("On-ground")[1:]
    assert any("all use the same slides" in p for p in master_deck.first_draft_problems(deck))


def test_a_source_may_not_be_one_of_hts_own_past_pitches():
    deck = _deck()
    deck["slides"][2] = {"layout": "stat-row", "heading": "Scale", "source": "HT PACE campaign proposals",
                         "stats": [{"value": "2,000+", "label": "Schools"}, {"value": "5L+", "label": "Students"}]}
    assert any("source talk about sources" in p for p in master_deck.first_draft_problems(deck))


def test_grid_cards_never_hold_label_value_lines():
    deck = _deck()
    deck["slides"][3]["cards"][0]["body"] = "**Student Reach:** 12 half-page features.\n\n**Gateways:** School heads."
    assert any("feature-grid): cards 0 use" in p for p in master_deck.first_draft_problems(deck))
    deck["slides"][3]["cards"][0]["body"] = "Twelve half-page features in the HT PACE School Edition."
    assert not any("feature-grid): cards" in p for p in master_deck.first_draft_problems(deck))


def test_an_ht_ip_carries_its_badge_on_every_one_of_its_slides():
    deck = _deck()
    for s in deck["slides"]:
        if s.get("eyebrow") == "Print":
            s["htIp"] = True
    assert not any("HT IP on some" in p for p in master_deck.first_draft_problems(deck))
    next(s for s in deck["slides"] if s.get("eyebrow") == "Print").pop("htIp")
    assert any('"Print" is an HT IP on some of its slides' in p for p in master_deck.first_draft_problems(deck))
