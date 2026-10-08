"""Readable brief references: "Tata Sampann 3" is Tata Sampann's third deck.

The brief ID (the email thread's ID, or the deck's file ID for a deck built
in chat) stays the key everywhere; the reference is what people see and
type. It is given once, when a deck is first saved, and never changes.
Typing it in chat, in any case and with spaces or hyphens, finds the deck.
"""

from __future__ import annotations

import re

from google.cloud import bigquery

from . import billing

_LEGAL = re.compile(r"\b(private|pvt|limited|ltd|llp|inc|corp|corporation)\b\.?", re.I)


def client_label(client_name: str) -> str:
    """The client as it reads in a reference: no bracketed notes, legal
    suffixes or stray punctuation. "Sheela Foam (Sleepwell)" stays whole."""
    name = re.sub(r"\(([^)]*)\)", lambda m: m.group(0) if len(m.group(1).split()) <= 2 else "", client_name or "")
    name = _LEGAL.sub("", name)
    name = re.sub(r"[^\w&()' ]+", " ", name)
    return re.sub(r"\s+", " ", name).strip() or "Unnamed"


def slug(text: str) -> str:
    """The same key whatever the case, spacing or punctuation:
    "Tata Sampann 3", "tata-sampann-3" and "TATA  SAMPANN 3" all match."""
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def _words(key: str) -> list[str]:
    words = [w[:-1] if len(w) > 3 and w.endswith("s") else w for w in key.split("-") if w]
    return words[1:] if words[:1] == ["the"] and len(words) > 1 else words


def _names(name: str) -> list[str]:
    """A name and any alternative given in brackets: "Sheela Foam (Sleepwell)"."""
    inside = re.findall(r"\(([^)]*)\)", name or "")
    return [re.sub(r"\([^)]*\)", " ", name or "")] + inside


def same_client(a: str, b: str) -> bool:
    """Two names for one client: the same words once case, punctuation, a
    leading "The", plurals and legal suffixes are ignored, or one is the
    other's bracketed name. "Sheela Foam (Sleepwell)" and "Sleepwell" match.
    Anything less is a different client: "Liberty" and "Liberty Shoes", or
    "Tata" and "Tata Sampann", start separate series, since a second series
    for one client is a cosmetic slip but a deck numbered into another
    client's series is wrong."""
    return any(_words(slug(_LEGAL.sub("", p))) == _words(slug(_LEGAL.sub("", q))) and _words(slug(p))
               for p in _names(a) for q in _names(b))


def assign(client: bigquery.Client, table: str, brief_id: str, client_name: str) -> str:
    """The brief's reference, given now if it has none. The number is one
    more than the references this client already has; if two decks for the
    same client are saved at once, the later one moves on to the next free
    number."""
    rows = list(client.query(
        f"SELECT brief_ref FROM `{table}` WHERE brief_id = @id AND brief_ref IS NOT NULL LIMIT 1",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", brief_id)]),
    ).result())
    if rows:
        return rows[0].brief_ref
    label = client_label(client_name)
    key = slug(label)
    # The client's existing series, if it was named a little differently
    # before: the new deck takes that series' name and next number.
    series = list(client.query(
        f"""SELECT client_slug, ANY_VALUE(REGEXP_REPLACE(brief_ref, r' [0-9]+$', '')) AS label, MIN(created_at) AS first
            FROM `{table}` WHERE brief_ref IS NOT NULL GROUP BY client_slug ORDER BY first""").result())
    match = next((r for r in series if same_client(r.client_slug, key)), None)
    if match:
        key, label = match.client_slug, match.label
    taken = {r.brief_ref for r in client.query(
        f"SELECT DISTINCT brief_ref FROM `{table}` WHERE client_slug = @key AND brief_ref IS NOT NULL",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("key", "STRING", key)]),
    ).result()}
    n = len(taken) + 1
    while f"{label} {n}" in taken:
        n += 1
    ref = f"{label} {n}"
    client.query(
        f"UPDATE `{table}` SET brief_ref = @ref, client_slug = @key WHERE brief_id = @id",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("ref", "STRING", ref),
            bigquery.ScalarQueryParameter("key", "STRING", key),
            bigquery.ScalarQueryParameter("id", "STRING", brief_id)]),
    ).result()
    # Two decks for one client saved at the same moment can both take n; the
    # one saved later (or with the larger ID) moves to the next free number.
    clash = list(client.query(
        f"""SELECT brief_id, MIN(created_at) AS first FROM `{table}` WHERE brief_ref = @ref
            GROUP BY brief_id ORDER BY first, brief_id""",
        job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("ref", "STRING", ref)]),
    ).result())
    if len(clash) > 1 and clash[0].brief_id != brief_id:
        client.query(f"UPDATE `{table}` SET brief_ref = NULL WHERE brief_id = @id",
                     job_config=bigquery.QueryJobConfig(query_parameters=[bigquery.ScalarQueryParameter("id", "STRING", brief_id)])).result()
        return assign(client, table, brief_id, client_name)
    return ref


def resolve(client: bigquery.Client, table: str, ref_or_id: str) -> str:
    """The brief ID for a reference ("Tata Sampann 3") or an ID; an unknown
    value is returned as it was, so the caller reports it as not found."""
    value = (ref_or_id or "").strip()
    rows = list(client.query(
        f"""SELECT brief_id FROM `{table}`
            WHERE brief_id = @v OR REGEXP_REPLACE(LOWER(brief_ref), r'[^a-z0-9]+', '-') = @s
            ORDER BY brief_id = @v DESC LIMIT 1""",
        job_config=bigquery.QueryJobConfig(query_parameters=[
            bigquery.ScalarQueryParameter("v", "STRING", value),
            bigquery.ScalarQueryParameter("s", "STRING", slug(value))]),
    ).result())
    return rows[0].brief_id if rows else value

