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
    long_body = "word " * 120
    deck["slides"][1]["body"] = long_body
    problems = master_deck.enforce(deck)
    assert any("slide 1 (two-column): body is 600 characters; the limit is 420" in p for p in problems)
    assert deck["slides"][1]["body"] == long_body


def test_list_items_are_bounded_in_count_and_length():
    deck = _deck()
    deck["slides"][3]["cards"] = [{"title": f"Card {i}"} for i in range(7)]
    deck["slides"][4]["steps"][0]["body"] = "x" * 121
    problems = master_deck.enforce(deck)
    assert any("cards has 7; it must have 2–6" in p for p in problems)
    assert any("steps[0].body is 121 characters" in p for p in problems)


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
    assert any("must have 7–20" in p for p in master_deck.enforce(deck))


def test_twenty_own_slides_pass_and_twenty_one_do_not():
    deck = _deck()
    filler = {"layout": "quote", "quote": "One more idea."}
    deck["slides"] = deck["slides"][:-2] + [dict(filler) for _ in range(20 - len(deck["slides"]))] + deck["slides"][-2:]
    assert len(deck["slides"]) == 20
    assert master_deck.enforce(deck) == []
    deck["slides"].insert(3, dict(filler))
    assert any("must have 7–20" in p for p in master_deck.enforce(deck))


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


def test_brief_and_next_steps_slides_make_their_point_in_an_aside_not_an_image():
    deck = _deck()
    nxt = len(deck["slides"]) - 2
    deck["slides"][nxt].update(image="placeholder", imageAlt="Calendar")
    deck["slides"][nxt].pop("aside", None)
    problems = master_deck.enforce(deck)
    assert any(f"slide {nxt} (next-steps) must use an aside" in p for p in problems)


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


def test_a_stat_row_without_a_source_is_rejected():
    deck = _deck()
    deck["slides"][2] = {"layout": "stat-row", "heading": "Why now",
                         "stats": [{"value": "100%", "label": "Purity"}, {"value": "4", "label": "Hubs"}]}
    assert any("needs its source in lead" in p for p in master_deck.enforce(deck))
    deck["slides"][2]["lead"] = "Source: Tata Consumer annual report 2024."
    assert master_deck.enforce(deck) == []


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
    assert heartland["lead"].startswith("Sources: IRS 2019")


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
    assert master_deck.first_draft_problems(deck) == []


def test_a_first_draft_needs_three_pictured_slides():
    deck = _deck()
    master_deck.enforce(deck)
    assert master_deck.first_draft_problems(deck) == []
    deck["slides"][7].pop("image")
    deck["slides"][7]["aside"] = "Kiranas."
    problems = master_deck.first_draft_problems(deck)
    assert any("2 slides with an image" in p and "at least 3" in p for p in problems)


def test_why_ht_logo_slots_do_not_count_toward_the_image_minimum():
    deck = _deck()
    for i in (7, 6):
        deck["slides"][i].pop("image")
        deck["slides"][i]["aside"] = "x"
    why_ht.insert(deck, [])
    master_deck.enforce(deck)
    assert any("1 slides with an image" in p for p in master_deck.first_draft_problems(deck))


def test_build_rejects_a_first_draft_short_of_images_before_rendering():
    deck = _deck()
    deck["slides"] = [s for s in deck["slides"] if s.get("layout") != "image-hero"]
    with patch.object(deck_tools, "_render_pptx") as render:
        result = deck_tools.build_solution_deck(json.dumps(deck), "Acme", "b1")
    render.assert_not_called()
    assert any("at least 3" in p for p in result["problems"])


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
