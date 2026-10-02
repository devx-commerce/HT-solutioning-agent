"""Revising a deck after it's built: slide operations, pictures, finding the deck.

The guarantees pinned here:
* a revision changes what it names and nothing else, and a call that can't
  be applied in full changes nothing;
* an image survives being round-tripped through the outline;
* a picture is generated only for a slide the revision itself touched;
* an image a person attaches is either placed, or refused with a reason
  they can act on, never silently squashed into a slot it doesn't fit.

Drive, BigQuery, the renderer and image generation are all mocked.
"""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.solutioning_agent.tools import deck as deck_tools
from agents.solutioning_agent.tools import master_deck, visuals

IMG_A = "data:image/jpeg;base64," + "A" * 400
IMG_B = "data:image/png;base64," + "B" * 400


def _deck():
    return {
        "type": "deck", "meta": {"theme": "ht-media"},
        "slides": [
            {"layout": "title", "heading": "Acme festive",
             "logos": [{"image": IMG_B, "alt": "HT Media logo"}, {"image": "placeholder", "alt": "Acme logo"}]},
            {"layout": "two-column", "heading": "The brief", "body": "Drive trial.", "aside": "8 weeks"},
            {"layout": "image-hero", "heading": "The idea", "image": IMG_A, "imageAlt": "A market"},
            {"layout": "two-column", "heading": "Canopies", "body": "x", "image": "placeholder://12:13",
             "imageAlt": "Image placeholder (12:13): a canopy"},
            {"layout": "quote", "quote": "Make the sale an event."},
            {"layout": "logo-wall", "heading": "Platforms", "cards": [
                {"title": "Hindustan", "image": IMG_A}, {"title": "Mint", "image": IMG_B}, {"title": "Fever FM"}]},
            {"layout": "two-column", "heading": "Next steps", "body": "Costing to follow.", "aside": "Pricing team."},
            {"layout": "closing", "heading": "Thank you"},
        ],
    }


@pytest.fixture
def published():
    """A stored deck, with publishing captured instead of performed."""
    state = {"stored": {"brief_id": "b1", "client_name": "Acme", "deck_file_id": "f1",
                        "deck_link": "https://docs.google.com/presentation/d/f1/edit",
                        "deck_json": json.dumps(_deck())},
             "saved": None}
    with patch.object(deck_tools, "_load_brief", side_effect=lambda bid: state["stored"]), \
         patch.object(deck_tools, "_render_pptx", return_value=b"PPTX") as render, \
         patch.object(deck_tools, "_upload_pptx") as upload, \
         patch.object(deck_tools, "_save_brief",
                      side_effect=lambda bid, **kw: state.update(saved=json.loads(kw["deck_json"]))):
        state["render"], state["upload"] = render, upload
        yield state


def _edit(edits):
    return deck_tools.update_deck("b1", json.dumps(edits))


# --- slide operations -----------------------------------------------------------


def test_a_slide_can_be_inserted_deleted_and_moved_in_one_publish(published):
    new = {"layout": "quote", "quote": "Every haat is a stage."}
    result = _edit([
        {"op": "insert", "position": 5, "slide": new},
        {"op": "delete", "slide_index": 4},
        {"op": "move", "slide_index": 4, "position": 2},
    ])
    assert result["edits_applied"] == [
        "inserted slide 5 (quote)", "deleted slide 4 (quote)", "moved slide 4 to 2"]
    published["upload"].assert_called_once()
    headings = [s.get("heading") or s.get("quote") for s in published["saved"]["slides"]]
    assert headings == ["Acme festive", "The brief", "Every haat is a stage.", "The idea",
                        "Canopies", "Platforms", "Next steps", "Thank you"]


def test_field_edits_and_structural_edits_combine_and_indices_follow_the_order(published):
    result = _edit([
        {"op": "delete", "slide_index": 4},
        {"slide_index": 4, "field": "heading", "value": "HT platforms"},  # the logo wall, now 4
    ])
    assert result["edits_applied"] == ["deleted slide 4 (quote)", "slide 4: heading"]
    assert published["saved"]["slides"][4]["heading"] == "HT platforms"


@pytest.mark.parametrize("edits, says", [
    ([{"op": "insert", "position": 99, "slide": {"layout": "quote", "quote": "x"}}], "insert position 99"),
    ([{"op": "insert", "position": 2, "slide": {"quote": "no layout"}}], "needs a"),
    ([{"op": "delete", "slide_index": 42}], "outside this deck"),
    ([{"op": "move", "slide_index": 3, "position": 8}], "move position 8"),
    ([{"op": "rename", "slide_index": 3}], "Unknown op"),
    ([{"slide_index": True, "field": "heading", "value": "x"}], "outside this deck"),
])
def test_an_edit_that_cannot_be_applied_changes_nothing(published, edits, says):
    result = _edit([{"slide_index": 1, "field": "heading", "value": "Changed"}, *edits])
    assert says in result["error"] and "Nothing was changed" in result["error"]
    published["upload"].assert_not_called()
    assert published["saved"] is None


def test_structural_edits_are_still_held_to_the_deck_rules(published):
    result = _edit([{"op": "move", "slide_index": 7, "position": 3}])  # closing into the middle
    assert "problems" in result
    published["upload"].assert_not_called()


# --- images survive the outline --------------------------------------------------


def _outline():
    return deck_tools.get_deck_outline("b1")["slides"]


def test_a_list_copied_back_from_the_outline_keeps_its_real_images(published):
    """The bug: rewriting a logo wall from the outline replaced its logos with text."""
    cards = _outline()[5]["cards"]
    assert cards[0]["image"].startswith("(embedded image ")
    cards = [c for c in cards if c["title"] != "Mint"] + [{"title": "Live Hindustan"}]
    _edit([{"slide_index": 5, "field": "cards", "value": cards}])
    saved = published["saved"]["slides"][5]["cards"]
    assert [c["title"] for c in saved] == ["Hindustan", "Fever FM", "Live Hindustan"]
    assert saved[0]["image"] == IMG_A  # the real image, not the outline's reference


def test_an_image_can_be_reused_on_another_slide_by_its_reference(published):
    ref = _outline()[2]["image"]
    _edit([{"slide_index": 3, "field": "image", "value": ref}])
    assert published["saved"]["slides"][3]["image"] == IMG_A


@pytest.mark.parametrize("value", ["(embedded image)", "(embedded image 0123456789)"])
def test_an_image_reference_not_in_the_deck_is_refused(published, value):
    result = _edit([{"slide_index": 2, "field": "image", "value": value}])
    assert "image references aren't in this deck" in result["error"]
    published["upload"].assert_not_called()


def test_the_cover_logos_survive_an_unrelated_edit(published):
    _edit([{"slide_index": 1, "field": "heading", "value": "New brief"}])
    assert published["saved"]["slides"][0]["logos"][0]["image"] == IMG_B


# --- pictures in a revision ------------------------------------------------------


def test_a_new_picture_is_generated_only_for_the_slide_the_edit_names(published):
    with patch.object(visuals, "generate_image", return_value="data:image/jpeg;base64,NEW") as gen:
        result = _edit([
            {"slide_index": 2, "field": "image", "value": "placeholder"},
            {"slide_index": 2, "field": "imageAlt", "value": "A wedding feast in Patna"},
        ])
    gen.assert_called_once_with("A wedding feast in Patna", "16:9")
    slides = published["saved"]["slides"]
    assert slides[2]["image"] == "data:image/jpeg;base64,NEW"
    assert slides[3]["image"] == "placeholder://12:13"  # untouched placeholder left alone
    assert result["images"] == {"generated": 1, "placeholders": 0}


def test_an_inserted_slide_gets_its_picture(published):
    slide = {"layout": "two-column", "heading": "Kiosks", "body": "x",
             "image": "placeholder", "imageAlt": "A kiosk at a metro exit"}
    with patch.object(visuals, "generate_image", return_value="data:image/jpeg;base64,K") as gen:
        _edit([{"op": "insert", "position": 4, "slide": slide}])
    gen.assert_called_once_with("A kiosk at a metro exit", "12:13")
    assert published["saved"]["slides"][4]["image"] == "data:image/jpeg;base64,K"


def test_a_text_edit_never_generates_a_picture(published):
    with patch.object(visuals, "generate_image") as gen:
        _edit([{"slide_index": 1, "field": "body", "value": "New body."}])
    gen.assert_not_called()


def test_a_deck_too_large_to_store_is_refused(published, monkeypatch):
    monkeypatch.setattr(deck_tools, "_MAX_STORED_DECK_CHARS", 1000)
    result = _edit([{"slide_index": 1, "field": "body", "value": "x"}])
    assert "too large to store" in result["error"]
    published["upload"].assert_not_called()


# --- finding the deck --------------------------------------------------------------


def _rows(*rows):
    from datetime import datetime, timezone
    out = []
    for i, (bid, title) in enumerate(rows):
        out.append({"brief_id": bid, "client_name": "Tata Sampann", "deck_file_id": f"f{i}",
                    "deck_link": f"https://docs.google.com/presentation/d/f{i}/edit",
                    "updated_at": datetime(2026, 9, 30 - i, 10, 0, tzinfo=timezone.utc),
                    "title": title, "has_deck_json": True})
    return out


def _lookup(rows, name="Tata"):
    with patch.object(deck_tools, "bigquery") as bq:
        bq.Client.return_value.query.return_value.result.return_value = rows
        result = deck_tools.lookup_deck(name)
        sql = bq.Client.return_value.query.call_args.args[0]
    return result, sql


def test_several_decks_for_a_client_are_all_listed_and_none_is_chosen():
    result, _ = _lookup(_rows(("t2", "Swad Ka Shagun"), ("t1", "Sampann Rasoi")))
    assert result["found"] is True
    assert [d["title"] for d in result["decks"]] == ["Swad Ka Shagun", "Sampann Rasoi"]
    assert result["decks"][0]["last_changed"] == "30 Sep 2026, 10:00 UTC"
    assert "brief_id" not in result  # the agent must ask which


def test_a_single_deck_is_also_given_at_the_top_level():
    result, _ = _lookup(_rows(("t1", "Sampann Rasoi")))
    assert result["brief_id"] == "t1" and result["link"].endswith("/f0/edit")


def test_no_deck_is_reported_as_not_found_and_the_match_is_partial():
    result, sql = _lookup([])
    assert result == {"found": False}
    assert "LIKE CONCAT('%', LOWER(@client_name), '%')" in sql


# --- an image the person attaches --------------------------------------------------


def _png(w, h):
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", w, h) + b"\x08\x06\x00\x00\x00"


@pytest.mark.parametrize("w, h, target, ok", [
    (1920, 1080, "image-hero", True),
    (1800, 1200, "image-hero", True),     # 3:2, a 16% crop
    (1600, 1200, "image-hero", False),    # 4:3, a 25% crop
    (1080, 1920, "image-hero", False),    # portrait in a widescreen slot
    (1100, 1200, "two-column", True),
    (1200, 1200, "two-column", True),
    (1920, 1080, "two-column", False),
    (640, 360, "image-hero", False),      # right shape, too few pixels
    (600, 200, "logo", True),
    (64, 64, "logo", False),
    (1200, 1200, "feature-grid", False),  # no picture slot
])
def test_an_upload_is_placed_only_where_it_fits(w, h, target, ok):
    uri, why = visuals.check_upload(_png(w, h), "image/png", target)
    assert bool(uri) is ok
    assert (why == "") is ok


def test_a_rejection_says_what_is_wrong_and_what_would_work():
    _, why = visuals.check_upload(_png(1600, 1200), "image/png", "image-hero")
    assert "1600×1200" in why and "4:3" in why and "16:9" in why and "25%" in why
    assert "1920×1080" in why


def test_every_problem_is_reported_at_once():
    """1024×768 for a widescreen slot is both too small and the wrong shape."""
    _, why = visuals.check_upload(_png(1024, 768), "image/png", "image-hero")
    assert "too small" in why and "crop away about 25%" in why


@pytest.mark.parametrize("data, mime, says", [
    (b"<svg></svg>", "image/svg+xml", "PNG or JPEG"),
    (b"RIFF....WEBPVP8 ", "image/webp", "PNG or JPEG"),
    (_png(1920, 1080) + b"\x00" * 4_100_000, "image/png", "MB"),
])
def test_unusable_files_are_refused_with_the_reason(data, mime, says):
    uri, why = visuals.check_upload(data, mime, "image-hero")
    assert uri is None and says in why


def _context(*, current=None, earlier=None, artifacts=None):
    def content(data, mime="image/png"):
        part = MagicMock()
        part.inline_data.data, part.inline_data.mime_type = data, mime
        part.file_data = None
        return MagicMock(parts=[part])

    ctx = MagicMock()
    ctx.user_content = content(current) if current else MagicMock(parts=[])
    events = []
    if earlier:
        events.append(MagicMock(author="user", content=content(earlier)))
    events.append(MagicMock(author="solutioning_agent", content=MagicMock(parts=[])))
    ctx.session.events = events
    names = list((artifacts or {}).keys())
    ctx.list_artifacts = AsyncMock(return_value=names)

    async def load(name):
        part = MagicMock()
        part.inline_data.data, part.inline_data.mime_type = artifacts[name], "image/png"
        return part
    ctx.load_artifact = AsyncMock(side_effect=load)
    return ctx


def _place(ctx, slide_index=2, target="slide_image"):
    return asyncio.run(deck_tools.place_image_from_chat("b1", slide_index, target, ctx))


def test_an_attached_image_that_fits_is_placed_on_the_slide(published):
    result = _place(_context(current=_png(1920, 1080)))
    assert result["placed"] == "picture on slide 2"
    image = published["saved"]["slides"][2]["image"]
    assert image == "data:image/png;base64," + base64.b64encode(_png(1920, 1080)).decode()


def test_an_attached_image_that_does_not_fit_is_refused_and_nothing_changes(published):
    result = _place(_context(current=_png(1600, 1200)))
    assert "crop away about 25%" in result["rejected"]
    assert result["deck_unchanged"] is True
    published["upload"].assert_not_called()


def test_a_slide_without_a_picture_slot_is_refused(published):
    result = _place(_context(current=_png(1920, 1080)), slide_index=4)  # the quote slide
    assert "no picture slot" in result["rejected"]
    published["upload"].assert_not_called()


def test_an_image_attached_in_an_earlier_message_is_found(published):
    result = _place(_context(earlier=_png(1100, 1200)), slide_index=3)
    assert result["placed"] == "picture on slide 3"


def test_an_image_saved_as_an_artifact_is_found(published):
    result = _place(_context(artifacts={"upload.png": _png(1920, 1080)}))
    assert result["placed"] == "picture on slide 2"


def test_no_attached_image_is_an_error_asking_for_one(published):
    result = _place(_context())
    assert "No image is attached" in result["error"]
    published["upload"].assert_not_called()


def test_an_attached_logo_replaces_the_client_logo_and_keeps_hts(published):
    result = _place(_context(current=_png(600, 200)), slide_index=0, target="client_logo")
    assert result["placed"] == "client logo on the cover"
    logos = published["saved"]["slides"][0]["logos"]
    assert logos[0]["image"] == IMG_B
    assert logos[1]["image"].startswith("data:image/png;base64,")
    assert logos[1]["alt"] == "Acme logo"


def test_the_upload_tool_hides_its_context_from_the_model():
    from google.adk.tools import FunctionTool

    schema = FunctionTool(deck_tools.place_image_from_chat)._get_declaration().parameters_json_schema
    assert set(schema["properties"]) == {"brief_id", "slide_index", "target"}
