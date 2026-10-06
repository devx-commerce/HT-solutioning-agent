"""The kinds of solution HT sells: each channel, HT's properties on it, and
its formats.

One list for the agent (to name every channel a brief asks for and give
each its own component) and for the past-deck index (to tag what each part
of a past deck is). "Integrated" is not a channel: it is a brief or a plan
that uses two or more of them.
"""

from __future__ import annotations

# channel -> (HT's properties on it, its formats)
CHANNELS: dict[str, tuple[str, str]] = {
    "Print": (
        "Hindustan Times, Hindustan, Mint, HT City and other supplements",
        "jacket or cover wrap, front-page solus, print innovation (die-cut, "
        "pasted insert, gatefold, flap, scented), branded editorial or "
        "advertorial, special supplement, contest or coupon, QR to digital",
    ),
    "Digital": (
        "hindustantimes.com, livehindustan.com, livemint.com, HT apps",
        "display or homepage takeover, native or branded articles, microsite "
        "or content hub, interactive tool (calculator, quiz, poll), contest, "
        "newsletter, WhatsApp or SMS outreach",
    ),
    "Video and social": (
        "HT's YouTube and social handles, HT Studio",
        "video series or docu-series, short-form reels, influencer or creator "
        "programme, social amplification, live stream",
    ),
    "Audio": (
        "Fever FM and HT's radio stations, HT Smartcast podcasts",
        "radio spots, RJ integration, on-air show, podcast",
    ),
    "Events and on-ground": (
        "HT PACE, Fresh on Campus, Anokhee Club, Hindustan Olympiad, HT and Mint summits",
        "summit or conclave, awards, school programme, campus festival, "
        "community club, competition or league, mall or market activation, "
        "sampling",
    ),
    "Content IP": (
        "HT's owned series and properties a brand integrates into",
        "brand integration in a recurring series or property",
    ),
    "Research and thought leadership": (
        "HT and Mint editorial and research",
        "survey, report, index, roundtable",
    ),
    "Social impact": (
        "HT's cause campaigns",
        "cause campaign or CSR programme",
    ),
}


def describe_for_agent() -> str:
    lines = ["HT's solution types, by channel (HT's properties on it: its formats):"]
    lines += [f"  - {name} ({where}): {formats}." for name, (where, formats) in CHANNELS.items()]
    return "\n".join(lines)
