"""Tests for the deck tools, pinned to the things that cost real damage.

Three behaviours here are load-bearing rather than merely correct:

  - An invalid deck must publish nothing at all. Rendering happens before
    any Drive or BigQuery call precisely so a schema error can't leave a
    half-written deck behind.
  - A revision must reuse the deck's file id. Links and read permissions
    are handed out to people; a new id on every edit silently breaks all
    of them.
  - An edit must touch only what it names. "Patch this slide" turning into
    "regenerate the deck" is how unreviewed changes reach a client draft.

The rest covers the input the model actually gets wrong in practice: raw
control characters inside JSON strings, and edits that aren't the shape the
tool documents.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agents.solutioning_agent.tools import deck as deck_tools


# A minimal deck that satisfies the master deck (see test_master_deck.py for
# the rules themselves), so these tests exercise publishing, not validation.
DECK = {
    "type": "deck",
    "meta": {"title": "T", "theme": "ht-media"},
    "slides": [
        {"layout": "title", "heading": "First", "lead": "keep me"},
        {"layout": "two-column", "heading": "Second", "body": "original body", "aside": "keep"},
        {"layout": "section", "heading": "Third"},
        {"layout": "quote", "quote": "One idea"},
        {"layout": "feature-grid", "columns": 2,
         "cards": [{"title": "A"}, {"title": "B"}]},
        {"layout": "two-column", "heading": "Next steps", "body": "Costing to follow.",
         "aside": "Pricing team to follow up."},
        {"layout": "closing", "heading": "Thank you"},
    ],
}
STORED = {
    "brief_id": "b1",
    "client_name": "Acme",
    "deck_file_id": "file-123",
    "deck_link": "https://docs.google.com/presentation/d/file-123/edit",
    "deck_json": json.dumps(DECK),
}


@pytest.fixture
def drive():
    """A Drive service whose create/update calls can be asserted on."""
    svc = MagicMock()
    svc.files().create().execute.return_value = {
        "id": "new-file", "webViewLink": "https://link/new"
    }
    svc.files().update().execute.return_value = {"id": "file-123"}
    svc.files.reset_mock()
    return svc


@pytest.fixture
def patched(drive):
    with patch.object(deck_tools, "build", return_value=drive), \
         patch.object(deck_tools, "get_credentials", return_value=None), \
         patch.object(deck_tools, "MediaFileUpload"), \
         patch.object(deck_tools, "bigquery") as bq, \
         patch.object(deck_tools, "_render_pptx", return_value=b"PPTX") as render:
        yield {"drive": drive, "bq": bq, "render": render}


# --- an invalid deck publishes nothing --------------------------------------


def test_render_failure_publishes_nothing_on_build(patched):
    patched["render"].side_effect = deck_tools.DeckRenderError("layout must be one of")

    result = deck_tools.build_solution_deck(json.dumps(DECK), "Acme", "b1")

    assert "error" in result
    assert "layout must be one of" in result["error"]
    patched["drive"].files.assert_not_called()
    patched["bq"].Client.assert_not_called()


def test_render_failure_leaves_an_existing_deck_untouched(patched):
    patched["render"].side_effect = deck_tools.DeckRenderError("bad edit")

    with patch.object(deck_tools, "_load_brief", return_value=STORED), \
         patch.object(deck_tools, "_save_brief") as save:
        result = deck_tools.update_deck(
            "b1", json.dumps([{"slide_index": 0, "field": "heading", "value": "X"}])
        )

    assert "error" in result
    save.assert_not_called()
    patched["drive"].files.assert_not_called()


# --- a revision keeps the file id -------------------------------------------


def test_update_replaces_content_and_never_creates_a_new_file(patched):
    with patch.object(deck_tools, "_load_brief", return_value=STORED), \
         patch.object(deck_tools, "_save_brief"):
        deck_tools.update_deck(
            "b1", json.dumps([{"slide_index": 0, "field": "heading", "value": "X"}])
        )

    update_kwargs = patched["drive"].files().update.call_args.kwargs
    assert update_kwargs["fileId"] == "file-123"
    patched["drive"].files().create.assert_not_called()


def test_build_creates_a_file_and_stores_its_id(patched):
    result = deck_tools.build_solution_deck(json.dumps(DECK), "Acme", "b1")

    assert result["deck_id"] == "new-file"
    assert result["brief_id"] == "b1"
    patched["drive"].files().update.assert_not_called()


def test_build_without_a_brief_id_falls_back_to_the_deck_id(patched):
    result = deck_tools.build_solution_deck(json.dumps(DECK), "Acme", "")
    assert result["brief_id"] == "new-file"


# --- edits touch only what they name ----------------------------------------


def test_only_named_fields_change(patched):
    saved = {}
    with patch.object(deck_tools, "_load_brief", return_value=STORED), \
         patch.object(deck_tools, "_save_brief",
                      side_effect=lambda bid, **kw: saved.update(kw)):
        deck_tools.update_deck(
            "b1", json.dumps([{"slide_index": 1, "field": "body", "value": "new body"}])
        )

    after = json.loads(saved["deck_json"])
    assert after["slides"][1]["body"] == "new body"
    # everything else byte-identical
    assert after["slides"][1]["heading"] == "Second"
    assert after["slides"][0] == DECK["slides"][0]
    assert after["slides"][2] == DECK["slides"][2]
    assert after["meta"] == DECK["meta"]


def test_several_edits_apply_together(patched):
    saved = {}
    with patch.object(deck_tools, "_load_brief", return_value=STORED), \
         patch.object(deck_tools, "_save_brief",
                      side_effect=lambda bid, **kw: saved.update(kw)):
        result = deck_tools.update_deck("b1", json.dumps([
            {"slide_index": 0, "field": "heading", "value": "A"},
            {"slide_index": 2, "field": "heading", "value": "B"},
        ]))

    after = json.loads(saved["deck_json"])
    assert [after["slides"][0]["heading"], after["slides"][2]["heading"]] == ["A", "B"]
    assert after["slides"][1] == DECK["slides"][1]
    assert result["edits_applied"] == ["slide 0: heading", "slide 2: heading"]


# --- malformed input is reported, not raised --------------------------------


@pytest.mark.parametrize("edits", [
    '[{"slide_index": 99, "field": "heading", "value": "x"}]',
    '[{"slide_index": -1, "field": "heading", "value": "x"}]',
    '[{"slide_index": "one", "field": "heading", "value": "x"}]',
    '[{"slide_index": 0, "value": "x"}]',
    '["just a string"]',
    '{"not": "an array"}',
    "not json at all",
])
def test_bad_edits_return_an_error_without_publishing(patched, edits):
    with patch.object(deck_tools, "_load_brief", return_value=STORED), \
         patch.object(deck_tools, "_save_brief") as save:
        result = deck_tools.update_deck("b1", edits)

    assert "error" in result
    save.assert_not_called()
    patched["drive"].files.assert_not_called()


def test_update_without_a_stored_deck_reports_it(patched):
    with patch.object(deck_tools, "_load_brief", return_value=None):
        assert "error" in deck_tools.update_deck("missing", "[]")


# --- deck json the model actually emits -------------------------------------


def test_raw_control_characters_are_survivable():
    raw = '{"type":"deck","slides":[{"layout":"title","heading":"A\nB"}]}'
    with pytest.raises(json.JSONDecodeError):
        json.loads(raw)

    fixed = deck_tools._normalize_deck_json(raw)

    assert json.loads(fixed)["slides"][0]["heading"] == "A\nB"


def test_genuinely_broken_json_is_left_for_the_renderer_to_explain():
    assert deck_tools._normalize_deck_json("{oh no") == "{oh no"


def test_stored_deck_is_the_normalized_one(patched):
    saved = {}
    with patch.object(deck_tools, "_save_brief",
                      side_effect=lambda bid, **kw: saved.update(kw)):
        # a literal newline inside a string value, as models emit
        raw = json.dumps(DECK).replace('"First"', '"A\nB"')
        deck_tools.build_solution_deck(raw, "Acme", "b1")

    # must be strictly parseable, or the next update_deck cannot read it back
    assert json.loads(saved["deck_json"])["slides"][0]["heading"] == "A\nB"


# --- persistence shape ------------------------------------------------------


def test_briefs_are_written_with_merge_not_a_streaming_insert(patched):
    deck_tools.build_solution_deck(json.dumps(DECK), "Acme", "b1")

    client = patched["bq"].Client.return_value
    client.insert_rows_json.assert_not_called()
    sql = client.query.call_args.args[0]
    assert "MERGE" in sql


# --- the outline that makes revision usable ---------------------------------


def test_outline_reports_index_layout_and_heading():
    with patch.object(deck_tools, "_load_brief", return_value=STORED):
        outline = deck_tools.get_deck_outline("b1")

    assert [s["slide_index"] for s in outline["slides"]] == list(range(7))
    assert outline["slides"][6]["layout"] == "closing"
    assert outline["slides"][6]["heading"] == "Thank you"
    assert "heading" in outline["slides"][0]["fields"]


def test_outline_without_a_stored_deck_reports_it():
    with patch.object(deck_tools, "_load_brief", return_value=None):
        assert "error" in deck_tools.get_deck_outline("missing")
