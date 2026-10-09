"""HT's past pitch decks, from the past_deck_slides table in BigQuery.

app/index_past_decks.py fills the table (daily, and on demand): one row per
slide with its text, the part of the deck it belongs to, the HT IPs,
solution types and print innovations tagged there, and an embedding. Search
ranks slides by meaning (embeddings) and by words (BM25), fuses the two
rankings and groups the slides by deck; read returns one deck whole;
print_formats lists every print innovation HT has proposed, with the decks. The table is small (a few
thousand rows at most), so it is loaded once an hour and ranked in memory.
"""

from __future__ import annotations

import math
import os
import re
import threading
import time
from collections import Counter

from . import billing

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
TABLE = os.environ.get("PAST_DECKS_TABLE", "")
EMBED_MODEL = "text-embedding-005"
EMBED_LOCATION = "us-central1"

# A deck whose best slide scores this close in meaning is usually on point;
# below it the match is loose (a related category, a shared word).
STRONG_MATCH = 0.6
_RELOAD_SECONDS = 3600
_TOP_SLIDES = 30   # slides from each ranking that go into the fusion
_MAX_DECKS = 5
_MAX_READ_CHARS = 40_000

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "rows": [], "index": None}


def embed(texts: list[str], task: str) -> list[list[float]]:
    """Unit-length embeddings for `texts` (task RETRIEVAL_DOCUMENT or RETRIEVAL_QUERY)."""
    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=PROJECT, location=EMBED_LOCATION)
    vectors = []
    for i in range(0, len(texts), 20):
        resp = client.models.embed_content(
            model=EMBED_MODEL, contents=texts[i:i + 20],
            # Embeddings take no billing labels; they cost a fraction of a paisa a search.
            config=types.EmbedContentConfig(task_type=task),
        )
        for e in resp.embeddings:
            norm = math.sqrt(sum(v * v for v in e.values)) or 1.0
            vectors.append([v / norm for v in e.values])
    return vectors


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class _Index:
    """BM25 over the slides' text, built once per load."""

    def __init__(self, rows: list[dict]):
        self.docs = [Counter(_words(r["context"] + "\n" + r["text"])) for r in rows]
        self.lengths = [sum(d.values()) for d in self.docs]
        self.avg = sum(self.lengths) / max(1, len(self.docs))
        self.df = Counter(w for d in self.docs for w in d)

    def scores(self, query: str) -> list[float]:
        n = len(self.docs)
        terms = set(_words(query))
        out = []
        for doc, length in zip(self.docs, self.lengths):
            score = 0.0
            for w in terms:
                tf = doc.get(w)
                if tf:
                    idf = math.log(1 + (n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                    score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * length / self.avg))
            out.append(score)
        return out


def _load() -> tuple[list[dict], _Index]:
    with _lock:
        if _cache["index"] is None or time.time() - _cache["at"] > _RELOAD_SECONDS:
            from google.cloud import bigquery

            client = bigquery.Client(project=PROJECT, default_query_job_config=billing.query_config())
            # Every column but the bookkeeping, so a column added later
            # (print_formats) is picked up without breaking older tables.
            rows = [dict(r) for r in client.query(
                f"SELECT * EXCEPT(modified_time, indexed_at) FROM `{TABLE}` ORDER BY deck_id, slide_no"
            ).result()]
            _cache.update(at=time.time(), rows=rows, index=_Index(rows))
        return _cache["rows"], _cache["index"]


def search(query: str, hidden: frozenset[str] = frozenset()) -> list[dict]:
    """The decks best matching `query`, best first, each with its best slides."""
    rows, index = _load()
    if not rows:
        return []
    qv = embed([query], "RETRIEVAL_QUERY")[0]
    meaning = [sum(a * b for a, b in zip(qv, r["embedding"])) for r in rows]
    words = index.scores(query)

    by_meaning = sorted(range(len(rows)), key=lambda j: -meaning[j])[:_TOP_SLIDES]
    by_words = [j for j in sorted(range(len(rows)), key=lambda j: -words[j])[:_TOP_SLIDES] if words[j] > 0]
    fused: Counter = Counter()
    for ranking in (by_meaning, by_words):  # reciprocal rank fusion
        for rank, j in enumerate(ranking):
            fused[j] += 1 / (60 + rank)

    decks: dict[str, dict] = {}
    for j, _ in fused.most_common():
        row = rows[j]
        if row["deck_id"] in hidden:
            continue
        deck = decks.get(row["deck_id"])
        if deck is None:
            if len(decks) == _MAX_DECKS:
                continue
            deck = decks[row["deck_id"]] = {
                "deck_id": row["deck_id"], "title": row["deck_name"], "link": row["link"],
                "score": 0.0, "slides": [], "ips": set(), "channels": set(), "print_formats": set(),
            }
        deck["score"] = max(deck["score"], meaning[j])
        deck["ips"].update(row["ips"] or [])
        deck["channels"].update(row["channels"] or [])
        deck["print_formats"].update(row.get("print_formats") or [])
        if len(deck["slides"]) < 3 and row["slide_no"] > 0:
            deck["slides"].append({"slide": row["slide_no"], "text": row["text"][:400]})
    return [
        {**d, "score": round(d["score"], 2), "match": "strong" if d["score"] >= STRONG_MATCH else "weak",
         "ips": sorted(d["ips"]), "channels": sorted(d["channels"]), "print_formats": sorted(d["print_formats"])}
        for d in decks.values()
    ]


def read(deck_id: str) -> dict | None:
    """One deck whole: its summary and every slide's text, in order."""
    rows, _ = _load()
    own = [r for r in rows if r["deck_id"] == deck_id]
    if not own:
        return None
    slides, used = [], 0
    for r in own:
        if r["slide_no"] == 0 or used > _MAX_READ_CHARS:
            continue
        slides.append({"slide": r["slide_no"], "part": r["context"].split("\n", 1)[-1], "text": r["text"]})
        used += len(r["text"])
    summary = next((r["text"] for r in own if r["slide_no"] == 0), "")
    return {
        "deck_id": deck_id, "title": own[0]["deck_name"], "link": own[0]["link"], "summary": summary,
        "ips": sorted({ip for r in own for ip in r["ips"] or []}),
        "channels": sorted({c for r in own for c in r["channels"] or []}),
        "print_formats": sorted({x for r in own for x in r.get("print_formats") or []}),
        "slides": slides, "complete": used <= _MAX_READ_CHARS,
    }


def print_formats(hidden: frozenset[str] = frozenset()) -> list[dict]:
    """Every print innovation HT's past decks propose, most used first, each
    with the decks and slides that show it. Names are grouped ignoring case
    and punctuation; each keeps its most common spelling."""
    rows, _ = _load()
    found: dict[str, dict] = {}
    for r in rows:
        if r["slide_no"] == 0 or r["deck_id"] in hidden:
            continue
        for name in r.get("print_formats") or []:
            key = "".join(_words(name))  # "Pull-Out", "Pullout" and "pull out" are one
            if not key:
                continue
            f = found.setdefault(key, {"spellings": Counter(), "decks": {}})
            f["spellings"][name] += 1
            deck = f["decks"].setdefault(r["deck_id"], {"deck_id": r["deck_id"], "title": r["deck_name"],
                                                        "link": r["link"], "slides": []})
            deck["slides"].append(r["slide_no"])
    out = [{"format": f["spellings"].most_common(1)[0][0], "decks": list(f["decks"].values())}
           for f in found.values()]
    return sorted(out, key=lambda f: (-len(f["decks"]), f["format"]))
