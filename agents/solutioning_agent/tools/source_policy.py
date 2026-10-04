"""Which publishers a deck may never cite: HT Media's direct competitors.

An HT pitch citing the Times of India or the Economic Times sends the client
to a rival's site, so web findings resting on these domains are dropped in
`research._grounded_findings`, where every web source already passes through
before the agent sees it. The agent's instruction names the same outlets,
generated from `OUTLETS`, so it doesn't name them in prose either.

The list is an assumption agreed for the pilot, not one HT has confirmed;
developer-docs/open-items.md tracks it for review. Change it here and nowhere else.
"""

from __future__ import annotations

import urllib.parse

# Outlet -> the domains it publishes on. A domain also covers its subdomains,
# so "indiatimes.com" catches timesofindia., economictimes., brandequity.
# economictimes. and navbharattimes.indiatimes.com.
OUTLETS: dict[str, tuple[str, ...]] = {
    "The Times of India / Times Group": (
        "indiatimes.com", "timesofindia.com", "economictimes.com",
        "maharashtratimes.com",
    ),
    "Dainik Jagran": ("jagran.com",),
    "Dainik Bhaskar": ("bhaskar.com", "divyabhaskar.co.in"),
    "Amar Ujala": ("amarujala.com",),
    "The Indian Express": ("indianexpress.com",),
    "The Hindu": ("thehindu.com",),
    "The Tribune": ("tribuneindia.com",),
}

_BLOCKED = frozenset(d for domains in OUTLETS.values() for d in domains)


def is_blocked(url: str) -> bool:
    """True when `url` is on a competitor's domain or one of its subdomains."""
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return False
    return any(host == d or host.endswith("." + d) for d in _BLOCKED)


def describe_for_agent() -> str:
    names = ", ".join(OUTLETS)
    return (
        f"Never cite, quote or name these publications, which compete with HT "
        f"Media: {names}. Their links are removed from search results before "
        "you see them. If a fact only appears in one of them, find another "
        "source or leave it out. HT's own titles (Hindustan Times, Hindustan, "
        "Mint, Live Hindustan) and trade press such as afaqs, exchange4media "
        "and Social Samosa are fine."
    )
