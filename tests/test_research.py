"""Tests for the research tools' two contractual guarantees.

The pilot is graded on "evidentiary grounding" and "retrieval transparency",
and both are enforced structurally in `tools/research.py` rather than by the
prompt, so both are pinned here:

* a finding is emitted only when it has BOTH claim text and at least one
  resolved source — a claim without sources, or a source list that is empty,
  must never reach the agent;
* every call writes exactly one `audit_log` row per source per brief, on the
  success path, the zero-results path and the failure path alike.

Telemetry is also tested for the inverse: a BigQuery outage must never turn
into a research failure.

Every external call (genai, Discovery Engine, YouTube, HTTP, BigQuery) is
mocked — this file runs offline.
"""

from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from agents.solutioning_agent.tools import research


# --- shared fakes ---------------------------------------------------------


def _urlopen_cm(resp):
    """urlopen() is always used as a context manager."""
    cm = MagicMock()
    cm.__enter__.return_value = resp
    cm.__exit__.return_value = False
    return cm


def _http_response(body: bytes = b"", url: str = "https://example.com", charset="utf-8"):
    resp = MagicMock()
    resp.url = url
    resp.read.return_value = body
    resp.headers.get_content_charset.return_value = charset
    return resp


def _chunk(uri, title="A title"):
    chunk = MagicMock()
    chunk.web.uri = uri
    chunk.web.title = title
    return chunk


def _webless_chunk():
    chunk = MagicMock()
    chunk.web = None
    return chunk


def _support(text, indices):
    support = MagicMock()
    support.segment.text = text
    support.grounding_chunk_indices = indices
    return support


def _grounded_response(chunks=(), supports=(), searches=("acme campaign",)):
    """Mimics resp.candidates[0].grounding_metadata as genai returns it."""
    resp = MagicMock()
    candidate = MagicMock()
    candidate.grounding_metadata.grounding_chunks = list(chunks)
    candidate.grounding_metadata.grounding_supports = list(supports)
    candidate.grounding_metadata.web_search_queries = list(searches)
    resp.candidates = [candidate]
    return resp


@contextmanager
def _genai_returning(resp=None, error=None):
    with patch("google.genai.Client") as client_cls:
        generate = client_cls.return_value.models.generate_content
        if error is not None:
            generate.side_effect = error
        else:
            generate.return_value = resp
        yield generate


@contextmanager
def _fake_discoveryengine(hits=(), error=None):
    module = MagicMock()
    client = module.SearchServiceClient.return_value
    if error is not None:
        client.search.side_effect = error
    else:
        client.search.return_value = list(hits)
    with patch.dict(sys.modules, {"google.cloud.discoveryengine_v1": module}):
        yield module


def _deck_hit(title="Acme 2024", link="https://drive/x", snippets=("a snippet",), doc=True):
    hit = MagicMock()
    if not doc:
        hit.document = None
        return hit
    hit.document.id = "doc-1"
    hit.document.derived_struct_data = {
        "title": title,
        "link": link,
        "snippets": [{"snippet": s} for s in snippets],
    }
    return hit


# --- _log_retrieval: the transparency row itself --------------------------


def test_log_retrieval_writes_exactly_one_audit_row():
    with patch.object(research, "bigquery") as bq, \
         patch.object(research, "PROJECT", "proj"), \
         patch.object(research, "DATASET", "ds"):
        research._log_retrieval("brief-1", "web_search", "success", 0.0, query="q")

    client = bq.Client.return_value
    client.insert_rows_json.assert_called_once()
    table, rows = client.insert_rows_json.call_args[0]
    assert table == "proj.ds.audit_log"
    assert len(rows) == 1
    assert rows[0]["brief_id"] == "brief-1"
    assert rows[0]["event_type"] == "retrieval"
    assert rows[0]["actor"] == "web_search"
    assert rows[0]["outcome"] == "success"
    assert rows[0]["recorded_at"]


def test_log_retrieval_detail_carries_duration_and_extras():
    with patch.object(research, "bigquery") as bq:
        research._log_retrieval("b", "youtube", "no_results", 0.0, query="q", result_count=0)

    detail = json.loads(bq.Client.return_value.insert_rows_json.call_args[0][1][0]["detail"])
    assert detail["query"] == "q"
    assert detail["result_count"] == 0
    assert isinstance(detail["duration_ms"], int)


def test_log_retrieval_blank_brief_id_becomes_null():
    """Ad-hoc research passes "" — the column is nullable, not empty-string."""
    with patch.object(research, "bigquery") as bq:
        research._log_retrieval("", "fetch_url", "success", 0.0)

    assert bq.Client.return_value.insert_rows_json.call_args[0][1][0]["brief_id"] is None


@pytest.mark.parametrize("attr", ["client", "insert"])
def test_log_retrieval_never_raises(attr):
    with patch.object(research, "bigquery") as bq:
        if attr == "client":
            bq.Client.side_effect = RuntimeError("no credentials")
        else:
            bq.Client.return_value.insert_rows_json.side_effect = RuntimeError("bq down")
        research._log_retrieval("b", "web_search", "success", 0.0)


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: research.search_past_decks("q", "b"), id="past_decks"),
        pytest.param(lambda: research.search_youtube("q", "b"), id="youtube"),
        pytest.param(lambda: research.fetch_url("not-a-url", "b"), id="fetch_url"),
    ],
)
def test_bigquery_outage_does_not_break_research(call):
    """Telemetry is best-effort; a dead BigQuery must not surface to the agent."""
    with patch.object(research, "bigquery") as bq, \
         patch.object(research, "PAST_DECKS_DATASTORE", ""), \
         patch.object(research, "YOUTUBE_API_KEY", ""):
        bq.Client.side_effect = RuntimeError("bq down")
        result = call()

    assert isinstance(result, dict)


def test_bigquery_outage_does_not_break_search_web():
    with patch.object(research, "bigquery") as bq, \
         _genai_returning(_grounded_response([_chunk("https://a.com")], [_support("Claim.", [0])])):
        bq.Client.side_effect = RuntimeError("bq down")
        result = research.search_web("acme", "b")

    assert result["findings"][0]["claim"] == "Claim."


# --- _resolve_redirect / _resolve_all -------------------------------------


@pytest.mark.parametrize(
    "url",
    ["https://acme.com/news", "http://example.org", "https://youtube.com/watch?v=1"],
)
def test_non_vertex_urls_are_returned_untouched(url):
    with patch("urllib.request.urlopen") as urlopen:
        assert research._resolve_redirect(url) == url
    urlopen.assert_not_called()


def test_vertex_redirect_resolves_to_the_real_url():
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    with patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(url="https://acme.com/real"))
        assert research._resolve_redirect(redirect) == "https://acme.com/real"


@pytest.mark.parametrize(
    "setup",
    [
        pytest.param("raise", id="network_error"),
        pytest.param("empty", id="no_final_url"),
    ],
)
def test_unresolvable_redirect_falls_back_to_original(setup):
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    with patch("urllib.request.urlopen") as urlopen:
        if setup == "raise":
            urlopen.side_effect = OSError("timed out")
        else:
            urlopen.return_value = _urlopen_cm(_http_response(url=""))
        assert research._resolve_redirect(redirect) == redirect


def test_resolve_all_with_no_urls_makes_no_threads():
    assert research._resolve_all([]) == {}


def test_resolve_all_maps_every_url():
    urls = ["https://a.com", "https://b.com"]
    assert research._resolve_all(urls) == {"https://a.com": "https://a.com",
                                           "https://b.com": "https://b.com"}


# --- search_web: evidentiary grounding ------------------------------------
# The core invariant: a finding exists only with claim text AND >= 1 source.


def test_finding_pairs_the_claim_with_its_source():
    resp = _grounded_response(
        [_chunk("https://acme.com/a", "Acme news")],
        [_support("Acme launched in June.", [0])],
    )
    with patch.object(research, "_log_retrieval"), _genai_returning(resp):
        result = research.search_web("acme", "b")

    assert result["findings"] == [
        {
            "claim": "Acme launched in June.",
            "sources": [{"url": "https://acme.com/a", "title": "Acme news"}],
        }
    ]


@pytest.mark.parametrize(
    "chunks, supports",
    [
        pytest.param([_chunk("https://a.com")], [_support("", [0])], id="empty_claim"),
        pytest.param([_chunk("https://a.com")], [_support("   ", [0])], id="blank_claim"),
        pytest.param([_chunk("https://a.com")], [_support(None, [0])], id="null_claim"),
        pytest.param([_chunk("https://a.com")], [_support("Claim.", None)], id="no_indices"),
        pytest.param([_chunk("https://a.com")], [_support("Claim.", [])], id="empty_indices"),
        pytest.param([_chunk("https://a.com")], [_support("Claim.", [7])], id="index_out_of_range"),
        pytest.param([_webless_chunk()], [_support("Claim.", [0])], id="chunk_without_web"),
        pytest.param([_chunk(None)], [_support("Claim.", [0])], id="chunk_without_uri"),
        pytest.param([], [_support("Claim.", [0])], id="no_chunks_at_all"),
    ],
)
def test_ungrounded_claims_are_never_returned(chunks, supports):
    with patch.object(research, "_log_retrieval"), \
         _genai_returning(_grounded_response(chunks, supports)):
        result = research.search_web("acme", "b")

    assert result["findings"] == []


def test_only_grounded_claims_survive_a_mixed_response():
    resp = _grounded_response(
        [_chunk("https://a.com", "A"), _chunk("https://b.com", "B")],
        [
            _support("Grounded claim.", [0]),
            _support("Ungrounded claim.", []),
            _support("", [1]),
            _support("Second grounded claim.", [1, 0]),
        ],
    )
    with patch.object(research, "_log_retrieval"), _genai_returning(resp):
        findings = research.search_web("acme", "b")["findings"]

    assert [f["claim"] for f in findings] == ["Grounded claim.", "Second grounded claim."]
    for finding in findings:
        assert finding["claim"].strip()
        assert finding["sources"]
        assert all(s["url"] for s in finding["sources"])


def test_out_of_range_index_is_dropped_but_valid_ones_kept():
    resp = _grounded_response(
        [_chunk("https://a.com", "A")],
        [_support("Claim.", [0, 99])],
    )
    with patch.object(research, "_log_retrieval"), _genai_returning(resp):
        sources = research.search_web("acme", "b")["findings"][0]["sources"]

    assert sources == [{"url": "https://a.com", "title": "A"}]


def test_duplicate_sources_are_deduplicated():
    resp = _grounded_response(
        [_chunk("https://a.com", "A"), _chunk("https://a.com", "A again")],
        [_support("Claim.", [0, 1, 0])],
    )
    with patch.object(research, "_log_retrieval"), _genai_returning(resp):
        sources = research.search_web("acme", "b")["findings"][0]["sources"]

    assert sources == [{"url": "https://a.com", "title": "A"}]


def test_missing_chunk_title_becomes_empty_string_not_none():
    resp = _grounded_response([_chunk("https://a.com", None)], [_support("Claim.", [0])])
    with patch.object(research, "_log_retrieval"), _genai_returning(resp):
        sources = research.search_web("acme", "b")["findings"][0]["sources"]

    assert sources == [{"url": "https://a.com", "title": ""}]


def test_sources_carry_the_resolved_url_not_the_expiring_redirect():
    """The citation trail has to survive the redirect expiring."""
    redirect = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"
    resp = _grounded_response([_chunk(redirect, "A")], [_support("Claim.", [0])])
    with patch.object(research, "_log_retrieval"), \
         patch("urllib.request.urlopen") as urlopen, \
         _genai_returning(resp):
        urlopen.return_value = _urlopen_cm(_http_response(url="https://acme.com/real"))
        sources = research.search_web("acme", "b")["findings"][0]["sources"]

    assert sources == [{"url": "https://acme.com/real", "title": "A"}]


def test_searches_actually_run_are_reported():
    resp = _grounded_response(
        [_chunk("https://a.com")], [_support("Claim.", [0])], searches=("acme ads", "acme 2026")
    )
    with patch.object(research, "_log_retrieval"), _genai_returning(resp):
        assert research.search_web("acme", "b")["searches_run"] == ["acme ads", "acme 2026"]


# --- search_web: retrieval transparency -----------------------------------


def test_search_web_logs_success_once():
    resp = _grounded_response([_chunk("https://a.com")], [_support("Claim.", [0])])
    with patch.object(research, "_log_retrieval") as log, _genai_returning(resp):
        research.search_web("acme", "brief-1")

    log.assert_called_once()
    args, kwargs = log.call_args
    assert args[:3] == ("brief-1", "web_search", "success")
    assert kwargs["result_count"] == 1


def test_search_web_logs_no_results_when_nothing_is_grounded():
    """A source that returned nothing is exactly what transparency records."""
    resp = _grounded_response([], [], searches=())
    with patch.object(research, "_log_retrieval") as log, _genai_returning(resp):
        result = research.search_web("acme", "brief-1")

    assert result == {"findings": [], "searches_run": []}
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "web_search", "no_results")
    assert log.call_args[1]["result_count"] == 0


def test_search_web_handles_a_response_with_no_candidates():
    resp = MagicMock()
    resp.candidates = []
    with patch.object(research, "_log_retrieval") as log, _genai_returning(resp):
        result = research.search_web("acme", "b")

    assert result == {"findings": [], "searches_run": []}
    assert log.call_args[0][2] == "no_results"


def test_search_web_reports_errors_instead_of_raising_and_logs_them():
    with patch.object(research, "_log_retrieval") as log, \
         _genai_returning(error=RuntimeError("quota exhausted")):
        result = research.search_web("acme", "brief-1")

    assert result["findings"] == []
    assert "quota exhausted" in result["error"]
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "web_search", "error")


# --- search_past_decks ----------------------------------------------------


def test_past_decks_unconfigured_is_an_explicit_error_and_is_logged():
    with patch.object(research, "PAST_DECKS_DATASTORE", ""), \
         patch.object(research, "_log_retrieval") as log:
        result = research.search_past_decks("acme", "brief-1")

    assert result["results"] == []
    assert "not connected" in result["error"]
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "error")


def test_past_decks_returns_title_link_and_snippet():
    hit = _deck_hit(snippets=("first bit", "second bit"))
    with patch.object(research, "PAST_DECKS_DATASTORE", "decks"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_in_past_decks_folder", return_value=True), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine([hit]):
        result = research.search_past_decks("acme", "brief-1")

    assert result["results"] == [
        {"title": "Acme 2024", "link": "https://drive/x", "snippet": "first bit second bit"}
    ]
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "success")


def test_past_decks_skips_hits_without_a_document():
    with patch.object(research, "PAST_DECKS_DATASTORE", "decks"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_in_past_decks_folder", return_value=True), \
         patch.object(research, "_log_retrieval"), \
         _fake_discoveryengine([_deck_hit(doc=False), _deck_hit()]):
        result = research.search_past_decks("acme", "b")

    assert len(result["results"]) == 1


def test_past_decks_falls_back_to_document_id_when_untitled():
    hit = _deck_hit()
    hit.document.derived_struct_data = {"link": "https://drive/x", "snippets": []}
    with patch.object(research, "PAST_DECKS_DATASTORE", "decks"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_in_past_decks_folder", return_value=True), \
         patch.object(research, "_log_retrieval"), \
         _fake_discoveryengine([hit]):
        result = research.search_past_decks("acme", "b")

    assert result["results"][0]["title"] == "doc-1"


def test_past_decks_logs_no_results_on_an_empty_corpus_hit():
    with patch.object(research, "PAST_DECKS_DATASTORE", "decks"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_in_past_decks_folder", return_value=True), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine([]):
        result = research.search_past_decks("acme", "brief-1")

    assert result == {"results": []}
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "no_results")


def test_past_decks_search_failure_is_reported_and_logged():
    with patch.object(research, "PAST_DECKS_DATASTORE", "decks"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_in_past_decks_folder", return_value=True), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine(error=RuntimeError("permission denied")):
        result = research.search_past_decks("acme", "brief-1")

    assert result["results"] == []
    assert "permission denied" in result["error"]
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "error")


# --- search_youtube -------------------------------------------------------


def test_youtube_unconfigured_is_an_explicit_error_and_is_logged():
    with patch.object(research, "YOUTUBE_API_KEY", ""), \
         patch.object(research, "_log_retrieval") as log:
        result = research.search_youtube("acme ad", "brief-1")

    assert result["results"] == []
    assert result["error"]
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "youtube", "error")


def test_youtube_builds_a_real_watch_url_per_video():
    payload = {
        "items": [
            {
                "id": {"videoId": "vid123"},
                "snippet": {
                    "title": "Acme ad",
                    "channelTitle": "Acme",
                    "publishedAt": "2026-01-01T00:00:00Z",
                },
            }
        ]
    }
    with patch.object(research, "YOUTUBE_API_KEY", "key"), \
         patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(json.dumps(payload).encode()))
        result = research.search_youtube("acme ad", "brief-1")

    assert result["results"] == [
        {
            "title": "Acme ad",
            "channel": "Acme",
            "published_at": "2026-01-01T00:00:00Z",
            "url": "https://www.youtube.com/watch?v=vid123",
        }
    ]
    assert log.call_args[0][:3] == ("brief-1", "youtube", "success")


def test_youtube_skips_items_without_a_video_id():
    payload = {"items": [{"id": {}, "snippet": {}}, {"snippet": {}}]}
    with patch.object(research, "YOUTUBE_API_KEY", "key"), \
         patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(json.dumps(payload).encode()))
        result = research.search_youtube("acme ad", "brief-1")

    assert result == {"results": []}
    assert log.call_args[0][:3] == ("brief-1", "youtube", "no_results")


def test_youtube_sends_the_query_and_key_in_the_request():
    with patch.object(research, "YOUTUBE_API_KEY", "secret-key"), \
         patch.object(research, "_log_retrieval"), \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(b'{"items": []}'))
        research.search_youtube("acme ad", "b")

    requested = urlopen.call_args[0][0].full_url
    assert "q=acme+ad" in requested
    assert "key=secret-key" in requested


def test_youtube_failure_is_reported_and_logged():
    with patch.object(research, "YOUTUBE_API_KEY", "key"), \
         patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.side_effect = OSError("connection reset")
        result = research.search_youtube("acme ad", "brief-1")

    assert result["results"] == []
    assert "connection reset" in result["error"]
    assert log.call_args[0][:3] == ("brief-1", "youtube", "error")


# --- fetch_url and _TextExtractor -----------------------------------------


@pytest.mark.parametrize(
    "url",
    ["acme.com", "ftp://acme.com/x", "file:///etc/passwd", "https://", "javascript:alert(1)"],
)
def test_fetch_url_rejects_non_http_urls_without_touching_the_network(url):
    with patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        result = research.fetch_url(url, "brief-1")

    urlopen.assert_not_called()
    assert result["error"] == "Not a valid http(s) url."
    assert result["url"] == url
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "fetch_url", "error")


def test_fetch_url_extracts_title_and_readable_text_only():
    html = (
        b"<html><head><title>Acme</title><style>body{color:red}</style></head>"
        b"<body><script>var x=1;</script><h1>Hello</h1><p>World</p>"
        b"<noscript>enable js</noscript></body></html>"
    )
    with patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(html, url="https://acme.com/final"))
        result = research.fetch_url("https://acme.com", "brief-1")

    assert result["title"] == "Acme"
    assert result["text"] == "Hello World"
    assert result["url"] == "https://acme.com/final"
    assert log.call_args[0][:3] == ("brief-1", "fetch_url", "success")


def test_fetch_url_truncates_very_long_pages():
    html = b"<html><body>" + b"word " * 20000 + b"</body></html>"
    with patch.object(research, "_log_retrieval"), \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(html))
        result = research.fetch_url("https://acme.com", "b")

    assert len(result["text"]) == research._MAX_PAGE_CHARS


def test_fetch_url_logs_no_results_for_a_page_with_no_readable_text():
    with patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.return_value = _urlopen_cm(_http_response(b"<html><body></body></html>"))
        result = research.fetch_url("https://acme.com", "brief-1")

    assert result["text"] == ""
    assert log.call_args[0][:3] == ("brief-1", "fetch_url", "no_results")


def test_fetch_url_failure_is_reported_and_logged():
    with patch.object(research, "_log_retrieval") as log, \
         patch("urllib.request.urlopen") as urlopen:
        urlopen.side_effect = OSError("404 Not Found")
        result = research.fetch_url("https://acme.com/missing", "brief-1")

    assert "404 Not Found" in result["error"]
    assert result["url"] == "https://acme.com/missing"
    assert log.call_args[0][:3] == ("brief-1", "fetch_url", "error")


def test_text_extractor_keeps_the_first_title_and_skips_noise():
    extractor = research._TextExtractor()
    extractor.feed(
        "<title>First</title><title>Second</title>"
        "<svg><text>icon</text></svg><p>Real copy</p>"
    )
    assert extractor.title == "First"
    assert "icon" not in extractor.parts
    assert "Real copy" in extractor.parts


# --- youtube titles reach a client-facing deck, so they must be readable ----


def test_youtube_titles_are_html_unescaped():
    payload = {
        "items": [
            {
                "id": {"videoId": "abc123"},
                "snippet": {
                    "title": "There&#39;s an Air about India &amp; more",
                    "channelTitle": "Air India &quot;Official&quot;",
                    "publishedAt": "2026-09-18T00:00:00Z",
                },
            }
        ]
    }
    with patch.object(research, "YOUTUBE_API_KEY", "key"), \
         patch.object(research, "_log_retrieval"), \
         patch.object(research.urllib.request, "urlopen") as urlopen:
        urlopen.return_value.__enter__.return_value.read.return_value = json.dumps(
            payload
        ).encode()
        result = research.search_youtube("air india", "b1")

    video = result["results"][0]
    assert video["title"] == "There's an Air about India & more"
    assert video["channel"] == 'Air India "Official"'


# --- past decks must never surface anything outside that folder ------------
# The Drive connector has no folder scoping, and the identity it indexes as
# can also see contract samples and internal sheets. Those appearing as
# "prior HT work" in a client pitch is the failure this guards against.


ALLOWED = frozenset({"1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456"})


@pytest.mark.parametrize("link,doc_id,expected", [
    ("https://drive.google.com/file/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456/view", "", True),
    ("", "1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456", True),
    ("https://docs.google.com/document/d/1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ/edit", "", False),
    ("", "", False),
    ("https://x/abc", "", False),
])
def test_only_files_in_the_decks_folder_pass_the_filter(link, doc_id, expected):
    assert research._in_past_decks_folder(ALLOWED, link, doc_id) is expected


def test_search_past_decks_drops_results_outside_the_folder():
    hit_ok = MagicMock()
    hit_ok.document.id = "d1"
    hit_ok.document.derived_struct_data = {
        "title": "Fortis X HT Media",
        "link": "https://drive.google.com/file/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456/view",
        "snippets": [{"snippet": "prior campaign"}],
    }
    hit_bad = MagicMock()
    hit_bad.document.id = "d2"
    hit_bad.document.derived_struct_data = {
        "title": "Sample of contract",
        "link": "https://docs.google.com/document/d/1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ/edit",
        "snippets": [{"snippet": "indemnity clause"}],
    }

    with patch.object(research, "PAST_DECKS_DATASTORE", "ds"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=ALLOWED), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_log_retrieval") as log, \
         patch.dict("sys.modules", {"google.cloud.discoveryengine_v1": MagicMock()}):
        from google.cloud import discoveryengine_v1 as de
        de.SearchServiceClient.return_value.search.return_value = [hit_ok, hit_bad]
        result = research.search_past_decks("fortis", "b1")

    titles = [r["title"] for r in result["results"]]
    assert titles == ["Fortis X HT Media"]
    assert "Sample of contract" not in titles
    assert log.call_args.kwargs["filtered_out"] == 1


def test_past_decks_retries_once_on_a_transient_failure():
    """A momentary network blip must not be reported as an unreachable corpus."""
    from google.api_core import exceptions as api_exceptions

    de = MagicMock()
    client = de.SearchServiceClient.return_value
    client.search.side_effect = [
        api_exceptions.ServiceUnavailable("dns hiccup"),
        [],  # second attempt succeeds
    ]
    with patch.object(research, "PAST_DECKS_DATASTORE", "ds"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_log_retrieval") as log, \
         patch.object(research.time, "sleep"), \
         patch.dict("sys.modules", {"google.cloud.discoveryengine_v1": de}):
        result = research.search_past_decks("fortis", "b1")

    assert client.search.call_count == 2
    assert "error" not in result
    assert log.call_args[0][2] == "no_results"


def test_past_decks_gives_up_after_the_second_transient_failure():
    from google.api_core import exceptions as api_exceptions

    de = MagicMock()
    de.SearchServiceClient.return_value.search.side_effect = \
        api_exceptions.ServiceUnavailable("still down")
    with patch.object(research, "PAST_DECKS_DATASTORE", "ds"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=frozenset()), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_log_retrieval") as log, \
         patch.object(research.time, "sleep"), \
         patch.dict("sys.modules", {"google.cloud.discoveryengine_v1": de}):
        result = research.search_past_decks("fortis", "b1")

    assert de.SearchServiceClient.return_value.search.call_count == 2
    assert "error" in result
    assert log.call_args[0][2] == "error"
