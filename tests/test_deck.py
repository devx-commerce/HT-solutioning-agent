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
import re
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
        {"layout": "image-hero", "heading": "Big idea", "image": "placeholder", "imageAlt": "A market"},
        {"layout": "two-column", "heading": "Pillar 1", "body": "x", "image": "placeholder", "imageAlt": "A van"},
        {"layout": "two-column", "heading": "Pillar 2", "body": "y", "image": "placeholder", "imageAlt": "A shop"},
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

    assert [s["slide_index"] for s in outline["slides"]] == list(range(len(DECK["slides"])))
    assert outline["slides"][-1]["layout"] == "closing"
    assert outline["slides"][-1]["heading"] == "Thank you"
    assert outline["slides"][0]["lead"] == "keep me"  # full content, not field names


def test_outline_without_a_stored_deck_reports_it():
    with patch.object(deck_tools, "_load_brief", return_value=None):
        assert "error" in deck_tools.get_deck_outline("missing")


# --- the outline is the deck's full content --------------------------------


def test_outline_carries_every_field_so_a_table_row_can_be_found():
    deck = json.loads(STORED["deck_json"])
    deck["slides"][4]["cards"][0]["body"] = "Live Hindustan native content"
    deck["slides"][1]["image"] = "data:image/png;base64," + "A" * 5000
    deck["slides"][1]["notes"] = "[why-ht:opener] internal"
    stored = {**STORED, "deck_json": json.dumps(deck)}
    with patch.object(deck_tools, "_load_brief", return_value=stored):
        outline = deck_tools.get_deck_outline("b1")
    slide = outline["slides"][4]
    assert slide["cards"][0]["body"] == "Live Hindustan native content"
    # No 5 KB blob, but a reference an edit can hand back.
    assert re.fullmatch(r"\(embedded image [0-9a-f]{10}\)", outline["slides"][1]["image"])
    assert "notes" not in outline["slides"][1]
    assert outline["slides"][0]["heading"] == "First"


def test_decks_are_shared_read_only_with_every_reader_domain(monkeypatch):
    monkeypatch.setattr(deck_tools, "DECK_READER_DOMAIN", "hindustantimes.com, htdigital.in")
    drive = MagicMock()
    drive.permissions().create().execute.side_effect = [OSError("first fails"), {"id": "p2"}]
    drive.permissions().create.reset_mock()
    deck_tools._lock_to_readers(drive, "f1")
    bodies = [c.kwargs["body"] for c in drive.permissions().create.call_args_list]
    assert bodies == [
        {"type": "domain", "role": "reader", "domain": "hindustantimes.com"},
        {"type": "domain", "role": "reader", "domain": "htdigital.in"},
    ]


def test_deploys_never_ship_eval_results_but_keep_the_env_file():
    """ADK's own .adk/ exclusion is broken (set('.adk/') is single characters)."""
    pytest.importorskip("click")
    from google.adk.cli.cli_deploy import _get_ignore_patterns_func

    ignore = _get_ignore_patterns_func("agents/solutioning_agent")
    ignored = ignore("agents/solutioning_agent", [".adk", ".env", "agent.py", "tools"])
    assert ".adk" in ignored
    assert ".env" not in ignored and "agent.py" not in ignored and "tools" not in ignored


class _FakeBucket:
    """Cloud Storage stand-in: name -> (bytes, content type)."""

    def __init__(self):
        self.objects, self.uploads = {}, 0

    def blob(self, name):
        bucket = self
        blob = MagicMock()
        blob.exists.side_effect = lambda: name in bucket.objects
        def upload(data, content_type):
            bucket.objects[name] = (data, content_type); bucket.uploads += 1
        blob.upload_from_string.side_effect = upload
        blob.download_as_bytes.side_effect = lambda: bucket.objects[name][0]
        type(blob).content_type = property(lambda self: bucket.objects[name][1])
        return blob


def test_pictures_are_saved_outside_the_deck_and_come_back_unchanged(monkeypatch):
    import base64 as b64
    pic = "data:image/jpeg;base64," + b64.b64encode(b"\xff\xd8 a picture").decode()
    deck = {"type": "deck", "slides": [{"layout": "two-column", "image": pic, "heading": "x"},
                                       {"layout": "image-hero", "image": pic}]}
    bucket = _FakeBucket()
    monkeypatch.setattr(deck_tools, "DECK_IMAGES_BUCKET", "decks")
    with patch("google.cloud.storage.Client") as client:
        client.return_value.bucket.return_value = bucket
        stored = deck_tools._save_images_to_storage(json.dumps(deck))
        assert "data:image" not in stored and stored.count("gs://decks/") == 2
        assert bucket.uploads == 1  # the same picture twice is stored once
        assert json.loads(deck_tools._load_stored_images(stored)) == deck
        deck_tools._save_images_to_storage(json.dumps(deck))
        assert bucket.uploads == 1  # and never uploaded again


def test_a_deck_saved_before_pictures_moved_out_loads_as_it_always_did(monkeypatch):
    monkeypatch.setattr(deck_tools, "DECK_IMAGES_BUCKET", "decks")
    inline = json.dumps({"slides": [{"image": "data:image/png;base64,AAAA"}]})
    assert deck_tools._load_stored_images(inline) == inline
