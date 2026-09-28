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
import urllib.error
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
def _fake_discoveryengine(answer=None, error=None):
    """Stands in for the v1alpha answer_query path.

    Not search: a FEDERATED Workspace store returns nothing from
    SearchService.search, so retrieval happens inside answer generation.
    """
    module = MagicMock()
    client = module.ConversationalSearchServiceClient.return_value
    if error is not None:
        client.answer_query.side_effect = error
    else:
        client.answer_query.return_value = MagicMock(answer=answer)
    with patch.dict(sys.modules, {"google.cloud.discoveryengine_v1alpha": module}):
        yield module


def _ref(title="Acme 2024", uri="https://drive/x", document="", content="a snippet"):
    reference = MagicMock()
    reference.chunk_info.content = content
    reference.chunk_info.document_metadata.title = title
    reference.chunk_info.document_metadata.uri = uri
    reference.chunk_info.document_metadata.document = document
    return reference


def _ref_without_metadata():
    reference = MagicMock()
    reference.chunk_info.document_metadata = None
    return reference


def _cite(start, end, reference_ids):
    citation = MagicMock()
    citation.start_index, citation.end_index = start, end
    citation.sources = [MagicMock(reference_id=r) for r in reference_ids]
    return citation


def _answer(text="", references=(), citations=()):
    answer = MagicMock()
    answer.answer_text = text
    answer.references = list(references)
    answer.citations = list(citations)
    return answer


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
         patch.object(research, "PAST_DECKS_ENGINE", ""), \
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


def _redirecting_to(location):
    """The grounding redirect answers 302 with the real url in Location."""
    return urllib.error.HTTPError(
        "https://vertexaisearch.cloud.google.com/r", 302, "Found",
        {"Location": location} if location is not None else {}, None,
    )


REDIRECT = "https://vertexaisearch.cloud.google.com/grounding-api-redirect/abc"


def test_vertex_redirect_resolves_to_the_real_url():
    with patch.object(research, "_no_redirect_opener") as opener:
        opener.open.side_effect = _redirecting_to("https://acme.com/real")
        assert research._resolve_redirect(REDIRECT) == "https://acme.com/real"


def test_redirect_resolves_even_when_the_destination_blocks_bots():
    """The destination is never requested, so its 403 or timeout can't matter."""
    with patch.object(research, "_no_redirect_opener") as opener, \
         patch("urllib.request.urlopen") as urlopen:
        opener.open.side_effect = _redirecting_to("https://blocks-bots.com/a")
        assert research._resolve_redirect(REDIRECT) == "https://blocks-bots.com/a"
    urlopen.assert_not_called()


@pytest.mark.parametrize(
    "location",
    [
        pytest.param(None, id="no_location_header"),
        pytest.param("", id="empty_location"),
        pytest.param("/relative/path", id="relative_location"),
        pytest.param(REDIRECT, id="redirects_to_another_redirect"),
        pytest.param("javascript:alert(1)", id="not_http"),
    ],
)
def test_unresolvable_redirect_is_none_never_the_expiring_link(location):
    with patch.object(research, "_no_redirect_opener") as opener:
        opener.open.side_effect = _redirecting_to(location)
        assert research._resolve_redirect(REDIRECT) is None


def test_network_error_retries_once_then_gives_none():
    with patch.object(research, "_no_redirect_opener") as opener:
        opener.open.side_effect = [OSError("timed out"), _redirecting_to("https://a.com/x")]
        assert research._resolve_redirect(REDIRECT) == "https://a.com/x"
        opener.open.side_effect = OSError("timed out")
        assert research._resolve_redirect(REDIRECT) is None


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
    resp = _grounded_response([_chunk(REDIRECT, "A")], [_support("Claim.", [0])])
    with patch.object(research, "_log_retrieval"), \
         patch.object(research, "_no_redirect_opener") as opener, \
         _genai_returning(resp):
        opener.open.side_effect = _redirecting_to("https://acme.com/real")
        sources = research.search_web("acme", "b")["findings"][0]["sources"]

    assert sources == [{"url": "https://acme.com/real", "title": "A"}]


def test_a_source_that_cannot_be_resolved_is_dropped_with_its_claim():
    resp = _grounded_response(
        [_chunk(REDIRECT, "Gone"), _chunk("https://kept.com", "Kept")],
        [_support("Only cites the dead link.", [0]),
         _support("Cites both.", [0, 1])],
    )
    with patch.object(research, "_log_retrieval") as log, \
         patch.object(research, "_resolve_redirect",
                      side_effect=lambda u: None if u == REDIRECT else u), \
         _genai_returning(resp):
        findings = research.search_web("acme", "b")["findings"]

    assert findings == [
        {"claim": "Cites both.", "sources": [{"url": "https://kept.com", "title": "Kept"}]}
    ]
    assert all("vertexaisearch" not in s["url"] for f in findings for s in f["sources"])
    assert log.call_args.kwargs["unresolved_sources"] == 1


def test_an_ungrounded_answer_is_retried_once():
    """The model sometimes answers from memory without searching."""
    ungrounded = _grounded_response([], [], searches=())
    grounded = _grounded_response([_chunk("https://a.com")], [_support("Claim.", [0])])
    with patch.object(research, "_log_retrieval") as log, _genai_returning() as generate:
        generate.side_effect = [ungrounded, grounded]
        result = research.search_web("acme", "b")

    assert generate.call_count == 2
    assert [f["claim"] for f in result["findings"]] == ["Claim."]
    assert log.call_args.kwargs["attempts"] == 2


def test_the_research_model_is_told_to_search_and_answer_only_from_results():
    resp = _grounded_response([_chunk("https://a.com")], [_support("Claim.", [0])])
    with patch.object(research, "_log_retrieval"), _genai_returning(resp) as generate:
        research.search_web("acme", "b")

    config = generate.call_args.kwargs["config"]
    assert config.system_instruction == research._GROUNDED_ONLY
    assert "memory" in research._GROUNDED_ONLY


def test_search_web_logs_the_urls_it_returned():
    resp = _grounded_response(
        [_chunk("https://b.com"), _chunk("https://a.com")],
        [_support("One.", [0]), _support("Two.", [1, 0])],
    )
    with patch.object(research, "_log_retrieval") as log, _genai_returning(resp):
        research.search_web("acme", "b")

    assert log.call_args.kwargs["source_urls"] == ["https://a.com", "https://b.com"]


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
    assert log.call_args[1]["attempts"] == 2
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
    with patch.object(research, "PAST_DECKS_ENGINE", ""), \
         patch.object(research, "_log_retrieval") as log:
        result = research.search_past_decks("acme", "brief-1")

    assert result["results"] == []
    assert "not connected" in result["error"]
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "error")


@contextmanager
def _past_decks_configured(allowed=frozenset(), in_folder=True):
    with patch.object(research, "PAST_DECKS_ENGINE", "decks"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=allowed), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_past_deck_file_id",
                      return_value="1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456" if in_folder else None):
        yield


def test_past_decks_returns_the_deck_and_the_claims_it_grounds():
    answer = _answer(
        text="Nukkad Natak toured 50 districts.",
        references=[_ref(content="Nukkad <b>Natak</b> &quot;plan&quot;")],
        citations=[_cite(0, 33, ["0"])],
    )
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "brief-1")

    assert result["results"] == [
        {"title": "Acme 2024", "link": "https://drive/x", "snippet": 'Nukkad Natak "plan"'}
    ]
    assert result["findings"] == [
        {
            "claim": "Nukkad Natak toured 50 districts.",
            "sources": [{"title": "Acme 2024", "link": "https://drive/x"}],
        }
    ]
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "success")


def test_past_decks_skips_references_without_document_metadata():
    answer = _answer(references=[_ref_without_metadata(), _ref()])
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "b")

    assert len(result["results"]) == 1
    assert log.call_args.kwargs["filtered_out"] == 1


def test_past_decks_falls_back_to_the_document_id_when_untitled():
    answer = _answer(references=[_ref(title="", uri="", document="a/b/documents/doc-1")])
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval"), \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "b")

    assert result["results"][0]["title"] == "doc-1"


def test_past_decks_dedupes_repeated_references_to_one_deck():
    """Every cited chunk is its own reference, so one deck arrives many times."""
    answer = _answer(
        references=[_ref(content="first bit"), _ref(content="second bit")],
    )
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval"), \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "b")

    assert len(result["results"]) == 1
    assert result["results"][0]["snippet"] == "first bit second bit"


def test_past_decks_logs_no_results_on_an_empty_corpus_hit():
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine(_answer()):
        result = research.search_past_decks("acme", "brief-1")

    assert result == {"results": [], "findings": []}
    log.assert_called_once()
    assert log.call_args[0][:3] == ("brief-1", "past_decks", "no_results")


def test_past_decks_answer_failure_is_reported_and_logged():
    with _past_decks_configured(), \
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
    assert bool(research._past_deck_file_id(ALLOWED, link, doc_id)) is expected


IN_FOLDER = "https://drive.google.com/file/d/1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456/view"
OUTSIDE = "https://docs.google.com/document/d/1QQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQQ/edit"


def test_search_past_decks_drops_results_outside_the_folder():
    answer = _answer(
        text="prior campaign. indemnity clause.",
        references=[
            _ref(title="Fortis X HT Media", uri=IN_FOLDER, content="prior campaign"),
            _ref(title="Sample of contract", uri=OUTSIDE, content="indemnity clause"),
        ],
        citations=[_cite(0, 16, ["0"]), _cite(17, 34, ["1"])],
    )
    with patch.object(research, "PAST_DECKS_ENGINE", "ds"), \
         patch.object(research, "PAST_DECKS_FOLDER_ID", "folder"), \
         patch.object(research, "_past_deck_file_ids", return_value=ALLOWED), \
         patch.object(research, "get_credentials", return_value=None), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("fortis", "b1")

    titles = [r["title"] for r in result["results"]]
    assert titles == ["Fortis X HT Media"]
    assert "Sample of contract" not in titles
    assert log.call_args.kwargs["filtered_out"] == 1
    # The filtered deck must not leak back in as an attributed claim either.
    assert [f["claim"] for f in result["findings"]] == ["prior campaign."]


def test_past_decks_never_reports_a_claim_without_a_source():
    """The grounding invariant: an uncitable span is dropped, not shown bare."""
    answer = _answer(
        text="A sourced claim. An unsourced claim.",
        references=[_ref()],
        citations=[_cite(0, 17, ["0"]), _cite(18, 36, [])],
    )
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval"), \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "b")

    assert [f["claim"] for f in result["findings"]] == ["A sourced claim."]


def test_past_decks_slices_claims_on_byte_offsets_not_characters():
    """Citation offsets are UTF-8 byte positions; slicing the str mangles them."""
    text = "Reach ₹500 crore readers."
    end = len(text.encode("utf-8"))
    answer = _answer(text=text, references=[_ref()], citations=[_cite(0, end, ["0"])])
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval"), \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "b")

    assert result["findings"][0]["claim"] == text


def test_past_decks_retries_once_on_a_transient_failure():
    """A momentary network blip must not be reported as an unreachable corpus."""
    from google.api_core import exceptions as api_exceptions

    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval") as log, \
         patch.object(research.time, "sleep"), \
         _fake_discoveryengine() as module:
        client = module.ConversationalSearchServiceClient.return_value
        client.answer_query.side_effect = [
            api_exceptions.ServiceUnavailable("dns hiccup"),
            MagicMock(answer=_answer()),  # second attempt succeeds
        ]
        result = research.search_past_decks("fortis", "b1")

    assert client.answer_query.call_count == 2
    assert "error" not in result
    assert log.call_args[0][2] == "no_results"


def test_past_decks_gives_up_after_the_second_transient_failure():
    from google.api_core import exceptions as api_exceptions

    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval") as log, \
         patch.object(research.time, "sleep"), \
         _fake_discoveryengine(
             error=api_exceptions.ServiceUnavailable("still down")) as module:
        result = research.search_past_decks("fortis", "b1")

    client = module.ConversationalSearchServiceClient.return_value
    assert client.answer_query.call_count == 2
    assert "error" in result
    assert log.call_args[0][2] == "error"


@pytest.mark.parametrize("configured,expected", [
    ("a,b", ("a", "b")),
    (" a , b ", ("a", "b")),
    ("a", ("a",)),
    ("a,,b", ("a", "b")),
    ("", ()),
])
def test_past_decks_folders_parses_a_comma_separated_allow_list(configured, expected):
    with patch.object(research, "PAST_DECKS_FOLDER_ID", configured):
        assert research._past_decks_folders() == expected


def test_past_deck_file_ids_collects_from_every_configured_folder():
    """Both folders hold the same decks; the connector returns either copy."""
    drive = MagicMock()
    drive.files.return_value.list.return_value.execute.side_effect = [
        {"files": [{"id": "one"}]},
        {"files": [{"id": "two"}]},
    ]
    research._past_deck_file_ids.cache_clear()
    with patch.object(research, "PAST_DECKS_FOLDER_ID", "folderA,folderB"), \
         patch.object(research, "get_credentials", return_value=None), \
         patch("googleapiclient.discovery.build", return_value=drive):
        assert research._past_deck_file_ids() == frozenset({"one", "two"})
    research._past_deck_file_ids.cache_clear()


def test_a_deck_with_no_uri_still_gets_an_openable_link():
    answer = _answer(references=[_ref(uri="", document="a/b/documents/1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456")])
    with _past_decks_configured(), \
         patch.object(research, "_log_retrieval") as log, \
         _fake_discoveryengine(answer):
        result = research.search_past_decks("acme", "b")

    link = "https://drive.google.com/open?id=1aBcDeFgHiJkLmNoPqRsTuVwXyZ0123456"
    assert result["results"][0]["link"] == link
    assert log.call_args.kwargs["source_urls"] == [link]
