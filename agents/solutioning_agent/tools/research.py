"""Research tools: past decks, the open web, YouTube, and a single page.

Every tool here returns claims already paired with the URL they came from,
because the pilot is graded on it — the SOW's "evidentiary grounding" metric
requires every draft to cite a real prior-campaign or competitor source, and
"retrieval transparency" requires each brief to state which sources returned
something and which didn't. Pairing happens in the tool rather than in the
prompt so the model physically cannot report a claim without its source.

Every call also writes one `audit_log` row per source per brief, including
calls that found nothing — a source returning zero results is exactly what
the transparency metric needs recorded.

Lives inside the agent's own package for the same reason tools/deck.py does:
`adk deploy agent_engine` bundles only this directory.
"""

from __future__ import annotations

import concurrent.futures
import functools
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser

from google.cloud import bigquery

from ..oauth_creds import get_credentials

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
# Separate from LOCATION: Gemini 3.x is served only from `global` here.
MODEL_LOCATION = os.environ.get("MODEL_LOCATION", LOCATION)
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")
RESEARCH_MODEL = os.environ.get("RESEARCH_MODEL", "gemini-2.5-flash")
# Searched through the Gemini Enterprise app, not the data store directly:
# the Drive connector runs in FEDERATED mode, querying Drive live rather than
# building an index, and that path is served by the app's serving config.
PAST_DECKS_ENGINE = os.environ.get("PAST_DECKS_ENGINE", "")
PAST_DECKS_LOCATION = os.environ.get("PAST_DECKS_LOCATION", "us")
# Comma-separated: the same decks live in more than one folder (an original
# and a working copy), and the connector returns whichever copy Drive's index
# prefers. Allow-listing only one silently drops about half the corpus.
PAST_DECKS_FOLDER_ID = os.environ.get("PAST_DECKS_FOLDER_ID", "")


def _past_decks_folders() -> tuple[str, ...]:
    return tuple(f.strip() for f in PAST_DECKS_FOLDER_ID.split(",") if f.strip())
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY", "")

_USER_AGENT = "Mozilla/5.0 (compatible; HT-SolutioningAgent/1.0)"
_FETCH_TIMEOUT = 15
_REDIRECT_TIMEOUT = 8
_MAX_PAGE_CHARS = 20000


def _log_retrieval(
    brief_id: str, source: str, outcome: str, started: float, **detail
) -> None:
    """One row per source per brief. Never raises — telemetry must not break research."""
    try:
        client = bigquery.Client(project=PROJECT)
        client.insert_rows_json(
            f"{PROJECT}.{DATASET}.audit_log",
            [
                {
                    "brief_id": brief_id or None,
                    "event_type": "retrieval",
                    "actor": source,
                    "outcome": outcome,
                    "detail": json.dumps(
                        {"duration_ms": int((time.time() - started) * 1000), **detail}
                    ),
                    "recorded_at": datetime.now(timezone.utc).isoformat(),
                }
            ],
        )
    except Exception:  # noqa: BLE001 - a telemetry failure is not a research failure
        pass


def _resolve_redirect(url: str) -> str:
    """Grounding returns expiring vertexaisearch redirect links; follow to the real one.

    The evidence summary lands in an email somebody may open weeks later, so a
    redirect that has since expired would break the citation trail.
    """
    if "vertexaisearch.cloud.google.com" not in url:
        return url
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_REDIRECT_TIMEOUT) as resp:
            return resp.url or url
    except Exception:  # noqa: BLE001 - an unresolvable redirect still works today
        return url


def _resolve_all(urls: list[str]) -> dict[str, str]:
    if not urls:
        return {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        return dict(zip(urls, pool.map(_resolve_redirect, urls)))


def search_web(query: str, brief_id: str) -> dict:
    """Search the open web for brand, campaign, competitor and social activity.

    Covers public posts on LinkedIn, Instagram, X and Facebook as well as news
    and brand sites, since Google indexes them. There is no separate social
    tool and no scraping.

    Args:
        query: what to find out, phrased as a question or search phrase.
        brief_id: the brief this research belongs to; pass "" for ad-hoc
            research not tied to a brief.

    Returns:
        findings, each a claim with the source urls it rests on, plus the
        searches actually run. Claims never appear without sources.
    """
    started = time.time()
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(vertexai=True, project=PROJECT, location=MODEL_LOCATION)
        resp = client.models.generate_content(
            model=RESEARCH_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                automatic_function_calling=types.AutomaticFunctionCallingConfig(
                    disable=True
                ),
            ),
        )
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not raised
        _log_retrieval(brief_id, "web_search", "error", started, query=query,
                       error=str(exc)[:300])
        return {"findings": [], "searches_run": [], "error": str(exc)[:300]}

    candidate = resp.candidates[0] if resp.candidates else None
    meta = getattr(candidate, "grounding_metadata", None) if candidate else None
    chunks = (getattr(meta, "grounding_chunks", None) or []) if meta else []
    supports = (getattr(meta, "grounding_supports", None) or []) if meta else []

    resolved = _resolve_all(
        [c.web.uri for c in chunks if getattr(c, "web", None) and c.web.uri]
    )

    def _sources_for(indices) -> list[dict]:
        out, seen = [], set()
        for i in indices or []:
            if i >= len(chunks) or not getattr(chunks[i], "web", None):
                continue
            url = resolved.get(chunks[i].web.uri, chunks[i].web.uri)
            if url and url not in seen:
                seen.add(url)
                out.append({"url": url, "title": chunks[i].web.title or ""})
        return out

    findings = []
    for support in supports:
        segment = getattr(support, "segment", None)
        claim = (getattr(segment, "text", "") or "").strip()
        sources = _sources_for(getattr(support, "grounding_chunk_indices", None))
        if claim and sources:
            findings.append({"claim": claim, "sources": sources})

    searches = list(getattr(meta, "web_search_queries", None) or []) if meta else []
    _log_retrieval(
        brief_id,
        "web_search",
        "success" if findings else "no_results",
        started,
        query=query,
        result_count=len(findings),
        searches_run=searches,
    )
    return {"findings": findings, "searches_run": searches}


@functools.lru_cache(maxsize=1)
def _past_deck_file_ids() -> frozenset[str]:
    """Drive file ids in the past-decks folder, as an allow-list.

    The Drive connector indexes everything the connecting identity can see —
    it offers no folder scoping — and that identity can also see contract
    samples and internal sheets. Those must never surface as "prior work" in
    a client pitch, so results are filtered to this folder rather than
    trusted to be decks.
    """
    from googleapiclient.discovery import build

    drive = build("drive", "v3", credentials=get_credentials())
    ids = set()
    for folder in _past_decks_folders():
        page = None
        while True:
            resp = drive.files().list(
                q=f"'{folder}' in parents and trashed=false",
                fields="nextPageToken, files(id)", pageSize=200, pageToken=page,
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            ids.update(f["id"] for f in resp.get("files", []))
            page = resp.get("nextPageToken")
            if not page:
                break
    return frozenset(ids)


_DRIVE_ID_RE = re.compile(r"[-\w]{25,}")


def _in_past_decks_folder(allowed: frozenset[str], *candidates: str) -> bool:
    """True only if some candidate string carries an allowed Drive file id.

    Fails closed: an unrecognisable result is dropped rather than shown.
    """
    for text in candidates:
        for token in _DRIVE_ID_RE.findall(text or ""):
            if token in allowed:
                return True
    return False


_MARKUP_RE = re.compile(r"</?b>")


def _clean_snippet(text: str) -> str:
    """Retrieved chunks arrive with <b> match markup and HTML entities."""
    return html.unescape(_MARKUP_RE.sub("", text or "")).strip()


def search_past_decks(query: str, brief_id: str) -> dict:
    """Search HT's own past pitch decks for relevant prior work.

    Args:
        query: what to look for: a client, an industry, a campaign type.
        brief_id: the brief this research belongs to; pass "" for ad-hoc
            research not tied to a brief.

    Returns:
        `results` (the past decks that matched, each with a link) and
        `findings`, each a claim paired with the decks it came from. Or an
        empty list with a reason when the corpus isn't reachable.
    """
    started = time.time()
    missing = [
        name for name, value in (
            ("PAST_DECKS_ENGINE", PAST_DECKS_ENGINE),
            # Required, not optional: without the folder allow-list every
            # result is filtered out, and a silent empty answer reads as
            # "no prior work exists" when it means "misconfigured".
            ("PAST_DECKS_FOLDER_ID", PAST_DECKS_FOLDER_ID),
        ) if not value
    ]
    if missing:
        _log_retrieval(brief_id, "past_decks", "error", started, query=query,
                       error=f"not configured: {', '.join(missing)}")
        return {
            "results": [],
            "error": "The past-decks corpus is not connected yet. Say so rather "
                     "than implying no prior work exists.",
        }

    serving_config = (
        f"projects/{PROJECT}/locations/{PAST_DECKS_LOCATION}"
        f"/collections/default_collection/engines/{PAST_DECKS_ENGINE}"
        f"/servingConfigs/default_search"
    )
    try:
        from google.api_core import exceptions as api_exceptions
        from google.cloud import discoveryengine_v1alpha as discoveryengine

        # The store is ACL-enabled (Google Identity), so it rejects a service
        # account — search as the shared identity that owns the decks folder.
        # A regional store is only reachable on its regional endpoint.
        client_options = (
            {"api_endpoint": f"{PAST_DECKS_LOCATION}-discoveryengine.googleapis.com"}
            if PAST_DECKS_LOCATION != "global" else None
        )
        # answer_query, not search. The Drive connector runs FEDERATED: it
        # queries Drive live and never builds an index, so SearchService.search
        # matches nothing and returns 0 results with HTTP 200 and no error —
        # which reads as "HT has no prior work" rather than "wrong API".
        # Retrieval only happens inside answer generation. v1alpha, not v1:
        # v1's answer_query returns "a summary could not be generated" against
        # this same serving config. Both verified against the live store.
        client = discoveryengine.ConversationalSearchServiceClient(
            credentials=get_credentials(), client_options=client_options
        )
        request = discoveryengine.AnswerQueryRequest(
            serving_config=serving_config,
            query=discoveryengine.Query(text=query),
            answer_generation_spec=(
                discoveryengine.AnswerQueryRequest.AnswerGenerationSpec(
                    include_citations=True
                )
            ),
            search_spec=discoveryengine.AnswerQueryRequest.SearchSpec(
                search_params=(
                    discoveryengine.AnswerQueryRequest.SearchSpec.SearchParams(
                        max_return_results=8
                    )
                )
            ),
        )
        # One retry on a transient failure. Observed twice in one session as a
        # DNS timeout inside gRPC, recovering immediately — without this the
        # agent reports the corpus unreachable, which reads as "no prior work
        # exists" rather than "the network blinked".
        for attempt in (1, 2):
            try:
                answer = client.answer_query(request).answer
                break
            except (api_exceptions.ServiceUnavailable,
                    api_exceptions.DeadlineExceeded,
                    api_exceptions.RetryError):
                if attempt == 2:
                    raise
                time.sleep(2)
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not raised
        _log_retrieval(brief_id, "past_decks", "error", started, query=query,
                       error=str(exc)[:300])
        return {"results": [], "error": str(exc)[:300]}

    allowed = _past_deck_file_ids() if PAST_DECKS_FOLDER_ID else frozenset()
    # A citation points at a reference by its index in this list, so the
    # position has to survive filtering — hence a dict keyed by index rather
    # than a list that would renumber once anything is dropped.
    sources_by_id: dict[str, dict] = {}
    decks: dict[str, dict] = {}
    filtered_out = 0
    for index, reference in enumerate(getattr(answer, "references", None) or []):
        chunk = getattr(reference, "chunk_info", None)
        meta = getattr(chunk, "document_metadata", None)
        if meta is None:
            filtered_out += 1
            continue
        link = getattr(meta, "uri", "") or ""
        document = getattr(meta, "document", "") or ""
        if not _in_past_decks_folder(allowed, link, document):
            filtered_out += 1
            continue
        title = getattr(meta, "title", "") or document.rsplit("/", 1)[-1]
        sources_by_id[str(index)] = {"title": title, "link": link}
        deck = decks.setdefault(
            link or title, {"title": title, "link": link, "snippet": ""}
        )
        snippet = _clean_snippet(getattr(chunk, "content", "") or "")
        if snippet and snippet not in deck["snippet"]:
            deck["snippet"] = (deck["snippet"] + " " + snippet).strip()[:800]

    # Citation offsets are byte positions into the UTF-8 answer, not character
    # positions — slicing the str directly mangles any non-ASCII claim.
    raw = (getattr(answer, "answer_text", "") or "").encode("utf-8")
    findings = []
    for citation in getattr(answer, "citations", None) or []:
        cited = [
            sources_by_id[key]
            for key in (
                str(getattr(source, "reference_id", ""))
                for source in (getattr(citation, "sources", None) or [])
            )
            if key in sources_by_id
        ]
        start = getattr(citation, "start_index", 0) or 0
        end = getattr(citation, "end_index", 0) or 0
        claim = raw[start:end].decode("utf-8", "ignore").strip()
        # Same invariant as search_web: a claim without an attributable
        # source is dropped, never reported bare.
        if claim and cited:
            findings.append({"claim": claim, "sources": cited})

    results = list(decks.values())
    _log_retrieval(
        brief_id,
        "past_decks",
        "success" if results else "no_results",
        started,
        query=query,
        result_count=len(results),
        filtered_out=filtered_out,
    )
    return {"results": results, "findings": findings}


def search_youtube(query: str, brief_id: str) -> dict:
    """Search YouTube for a brand's or competitor's video and ad activity.

    Args:
        query: what to search for, e.g. a brand name plus "campaign" or "ad".
        brief_id: the brief this research belongs to; pass "" for ad-hoc
            research not tied to a brief.

    Returns:
        matching videos with title, channel, publish date and a real watch
        url, or an empty list with a reason when YouTube isn't reachable.
    """
    started = time.time()
    if not YOUTUBE_API_KEY:
        _log_retrieval(brief_id, "youtube", "error", started, query=query,
                       error="YOUTUBE_API_KEY not configured")
        return {"results": [], "error": "YouTube search is not configured yet."}

    params = urllib.parse.urlencode(
        {
            "part": "snippet",
            "q": query,
            "type": "video",
            "maxResults": 8,
            "key": YOUTUBE_API_KEY,
        }
    )
    try:
        req = urllib.request.Request(
            f"https://www.googleapis.com/youtube/v3/search?{params}",
            headers={"User-Agent": _USER_AGENT},
        )
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not raised
        _log_retrieval(brief_id, "youtube", "error", started, query=query,
                       error=str(exc)[:300])
        return {"results": [], "error": str(exc)[:300]}

    results = []
    for item in payload.get("items", []):
        video_id = (item.get("id") or {}).get("videoId")
        snippet = item.get("snippet") or {}
        if not video_id:
            continue
        results.append(
            {
                # YouTube returns titles HTML-escaped; a deck slide reading
                # "There&#39;s an Air about India" is not shippable.
                "title": html.unescape(snippet.get("title", "")),
                "channel": html.unescape(snippet.get("channelTitle", "")),
                "published_at": snippet.get("publishedAt", ""),
                "url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )

    _log_retrieval(
        brief_id,
        "youtube",
        "success" if results else "no_results",
        started,
        query=query,
        result_count=len(results),
    )
    return {"results": results}


class _TextExtractor(HTMLParser):
    """Readable text and <title> only — script/style content is noise for research."""

    _SKIP = {"script", "style", "noscript", "svg", "head"}

    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.title = ""
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        elif tag == "title":
            self._in_title = False

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self._in_title and not self.title:
            self.title = text
        elif not self._skip_depth:
            self.parts.append(text)


def fetch_url(url: str, brief_id: str) -> dict:
    """Read one specific web page, typically the client's own site.

    Only call this with a url you already have: one from the email thread, or
    one a search result returned. Never guess a domain from a company name.

    Args:
        url: the exact page to read, including https://.
        brief_id: the brief this research belongs to; pass "" for ad-hoc
            research not tied to a brief.

    Returns:
        the page's title and readable text, with the url it actually came
        from after any redirects.
    """
    started = time.time()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _log_retrieval(brief_id, "fetch_url", "error", started, url=url,
                       error="not an http(s) url")
        return {"url": url, "error": "Not a valid http(s) url."}

    try:
        req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT) as resp:
            final_url = resp.url or url
            charset = resp.headers.get_content_charset() or "utf-8"
            body = resp.read(2_000_000).decode(charset, errors="replace")
    except Exception as exc:  # noqa: BLE001 - surfaced to the agent, not raised
        _log_retrieval(brief_id, "fetch_url", "error", started, url=url,
                       error=str(exc)[:300])
        return {"url": url, "error": str(exc)[:300]}

    extractor = _TextExtractor()
    extractor.feed(body)
    text = " ".join(extractor.parts)[:_MAX_PAGE_CHARS]

    _log_retrieval(
        brief_id,
        "fetch_url",
        "success" if text else "no_results",
        started,
        url=url,
        final_url=final_url,
        chars=len(text),
    )
    return {"url": final_url, "title": extractor.title, "text": text}
