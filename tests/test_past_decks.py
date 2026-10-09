"""The past-deck index: search and read over the table, the switch between
it and the Drive connector, and how the indexer reads and tags a deck."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import patch

import pytest

from agents.solutioning_agent import index_past_decks as indexer
from agents.solutioning_agent.tools import past_decks, research


def _row(deck, slide, text, vec, part="", ips=(), channels=()):
    return {"deck_id": deck, "deck_name": f"{deck} deck", "link": f"https://drive/{deck}", "slide_no": slide,
            "context": f"{deck} deck\n{part}", "text": text, "ips": list(ips), "channels": list(channels),
            "embedding": vec}


ROWS = [
    _row("pace", 0, "Sensodyne in schools through HT PACE.", [1, 0, 0]),
    _row("pace", 1, "HT PACE school contact programme across 200 schools", [0.9, 0.1, 0], "HT PACE schools", ["HT PACE"], ["Events and on-ground"]),
    _row("pace", 2, "Guest highlights from past meets", [0.8, 0.2, 0], "HT PACE schools", ["HT PACE"], ["Events and on-ground"]),
    _row("nupur", 1, "Anokhee Club festive meets for women in UP", [0, 1, 0], "Anokhee Club", ["Anokhee Club"], ["Events and on-ground"]),
    _row("gold", 1, "Gold ETF explainers in Mint", [0, 0, 1], "Mint explainers", [], ["Print"]),
]


@pytest.fixture
def table():
    index = past_decks._Index(ROWS)
    with patch.object(past_decks, "_load", return_value=(ROWS, index)):
        yield


def test_search_groups_slides_by_deck_best_first(table):
    with patch.object(past_decks, "embed", return_value=[[1, 0, 0]]):
        results = past_decks.search("school programme HT PACE")
    assert results[0]["deck_id"] == "pace"
    assert [s["slide"] for s in results[0]["slides"]] == [1, 2]  # never the summary row
    assert results[0]["ips"] == ["HT PACE"] and results[0]["match"] == "strong"
    assert len({r["deck_id"] for r in results}) == len(results)


def test_a_loose_match_is_marked_weak(table):
    with patch.object(past_decks, "embed", return_value=[[0.3, 0.3, 0.3]]):
        results = past_decks.search("Yas Island proposal")
    assert results and all(r["match"] == "weak" for r in results)


def test_words_find_a_deck_meaning_alone_ranks_low(table):
    with patch.object(past_decks, "embed", return_value=[[1, 0, 0]]):
        results = past_decks.search("Anokhee Club")
    assert "nupur" in [r["deck_id"] for r in results]


def test_hidden_decks_never_come_back(table):
    with patch.object(past_decks, "embed", return_value=[[1, 0, 0]]):
        assert "pace" not in [r["deck_id"] for r in past_decks.search("HT PACE", frozenset({"pace"}))]


def test_read_returns_the_deck_in_order_with_its_summary(table):
    deck = past_decks.read("pace")
    assert deck["summary"].startswith("Sensodyne")
    assert [s["slide"] for s in deck["slides"]] == [1, 2] and deck["slides"][0]["part"] == "HT PACE schools"
    assert past_decks.read("nope") is None


def test_the_switch_sends_search_to_the_table(monkeypatch):
    monkeypatch.setattr(research, "PAST_DECKS_SOURCE", "bigquery")
    found = [{"deck_id": "pace", "link": "https://drive/pace", "match": "strong"}]
    with patch.object(past_decks, "search", return_value=found) as search, \
         patch.object(research, "_log_retrieval"):
        assert research.search_past_decks("HT PACE", "b1") == {"results": found}
    search.assert_called_once()


def test_a_table_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(research, "PAST_DECKS_SOURCE", "bigquery")
    with patch.object(past_decks, "search", side_effect=RuntimeError("no table")), \
         patch.object(research, "_log_retrieval"):
        result = research.search_past_decks("HT PACE", "b1")
    assert result["results"] == [] and "not implying no prior work" not in result["error"]
    assert "could not be searched" in result["error"]


def test_print_formats_group_spellings_and_list_their_decks():
    rows = [
        {**_row("nissan", 0, "Summary", [1, 0, 0]), "print_formats": ["French Window", "Gatefold"]},
        {**_row("nissan", 5, "FRENCH WINDOW", [1, 0, 0]), "print_formats": ["French Window"]},
        {**_row("nissan", 6, "6 PAGE GATE FOLD", [1, 0, 0]), "print_formats": ["Gatefold"]},
        {**_row("bkt", 11, "Perforated French window", [1, 0, 0]), "print_formats": ["French window"]},
        _row("pace", 1, "HT PACE schools", [1, 0, 0]),  # indexed before the column existed
    ]
    with patch.object(past_decks, "_load", return_value=(rows, past_decks._Index(rows))):
        found = past_decks.print_formats()
        hidden = past_decks.print_formats(frozenset({"bkt"}))
    assert [f["format"] for f in found] == ["French Window", "Gatefold"]  # most decks first
    assert [(d["deck_id"], d["slides"]) for d in found[0]["decks"]] == [("nissan", [5]), ("bkt", [11])]
    assert [d["deck_id"] for d in hidden[0]["decks"]] == ["nissan"]


def test_list_print_formats_needs_the_table(monkeypatch):
    monkeypatch.setattr(research, "PAST_DECKS_SOURCE", "vertex")
    assert research.list_print_formats("b1")["formats"] == []
    monkeypatch.setattr(research, "PAST_DECKS_SOURCE", "bigquery")
    with patch.object(past_decks, "print_formats", return_value=[{"format": "Gatefold", "decks": []}]), \
         patch.object(research, "_log_retrieval"):
        assert research.list_print_formats("b1")["formats"][0]["format"] == "Gatefold"


def test_read_past_deck_keeps_an_eval_cases_own_deck_hidden():
    ctx = type("Ctx", (), {"state": {research.EVAL_HIDDEN_DECKS_KEY: ["pace"]}})()
    with patch.object(past_decks, "read") as read:
        assert "error" in research.read_past_deck("pace", "b1", ctx)
    read.assert_not_called()


# --- the indexer ---------------------------------------------------------------------

def _pptx(slides_in_order: list[tuple[str, str]]) -> bytes:
    """A minimal pptx whose slide files are numbered out of presentation order."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        rels = "".join(f'<Relationship Id="rId{i}" Type="slide" Target="slides/{f}"/>'
                       for i, (f, _) in enumerate(slides_in_order, 2))
        z.writestr("ppt/_rels/presentation.xml.rels", f"<Relationships>{rels}</Relationships>")
        ids = "".join(f'<p:sldId id="{255 + i}" r:id="rId{i}"/>' for i in range(2, len(slides_in_order) + 2))
        z.writestr("ppt/presentation.xml", f"<p:presentation><p:sldIdLst>{ids}</p:sldIdLst></p:presentation>")
        for f, text in slides_in_order:
            paras = "".join(f"<a:p><a:r><a:t>{line}</a:t></a:r></a:p>" for line in text.split("\n"))
            z.writestr(f"ppt/slides/{f}", f"<p:sld>{paras}</p:sld>")
    return buf.getvalue()


def test_pptx_slides_follow_the_presentation_order_not_file_names():
    data = _pptx([("slide9.xml", "Cover"), ("slide2.xml", "The brief\nSchools &amp; families")])
    assert indexer.pptx_slides(data) == ["Cover", "The brief\nSchools & families"]


def test_a_pdf_page_with_no_text_is_read_as_a_picture():
    from pypdf import PdfWriter

    w = PdfWriter()
    w.add_blank_page(width=960, height=540)
    buf = io.BytesIO()
    w.write(buf)
    calls = []
    pages = indexer.pdf_pages(buf.getvalue(), lambda page: calls.append(page) or "HT Golf Ecosystem")
    assert pages == ["HT Golf Ecosystem"] and calls and calls[0].startswith(b"%PDF")


def test_rows_carry_each_slides_part_even_where_the_slide_never_names_it():
    f = {"id": "d1", "name": "Rocksport X HT.pptx", "webViewLink": "https://drive/d1", "modifiedTime": "t"}
    tags = {"summary": "Rocksport across schools and families.", "parts": [
        {"first_slide": 1, "last_slide": 1, "title": "Cover", "ips": [], "channels": []},
        {"first_slide": 2, "last_slide": 3, "title": "HT PACE Principals' Meet", "ips": ["HT PACE"],
         "channels": ["Events and on-ground"]},
    ]}
    with patch.object(past_decks, "embed", side_effect=lambda texts, task: [[0.0]] * len(texts)):
        rows = indexer.rows_for(f, ["Cover", "HT PACE meet", "Blast from the past: guests", ""], tags, "now")
    assert [r["slide_no"] for r in rows] == [0, 1, 2, 3]  # the empty slide 4 is skipped
    assert rows[3]["ips"] == ["HT PACE"] and rows[3]["context"] == "Rocksport X HT\nHT PACE Principals' Meet"
    assert rows[0]["text"].startswith("Rocksport") and rows[0]["ips"] == ["HT PACE"]


def test_rows_carry_the_print_formats_of_their_part():
    f = {"id": "d1", "name": "Nissan.pptx", "webViewLink": "https://drive/d1", "modifiedTime": "t"}
    tags = {"summary": "Nissan launch.", "parts": [
        {"first_slide": 1, "last_slide": 2, "title": "Launch print innovations", "ips": [], "channels": ["Print"],
         "print_formats": ["French Window"]}]}
    with patch.object(past_decks, "embed", side_effect=lambda texts, task: [[0.0]] * len(texts)):
        rows = indexer.rows_for(f, ["FRENCH WINDOW", "Spread"], tags, "now")
    assert rows[0]["print_formats"] == ["French Window"] and rows[2]["print_formats"] == ["French Window"]
