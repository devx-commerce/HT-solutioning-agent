"""The solutioning agent — deck generation is still a placeholder here on purpose.

Built first for the deployment path to actually work end to end: registered
and invoked from a Gemini Enterprise chat app, in HT's project, under
whatever entitlements and IAM that project actually has. Research, evidence,
and real deck content are `docs/deck-agent/`'s job, built as its own piece
rather than bolted on here.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.agents import LlmAgent

# adk deploy agent_engine bundles this directory's files into the deployed
# source, but does not read a .env for you — nothing loads it without this
# call. tools/deck.py needs TEMPLATE_FILE_ID and OAUTH_TOKEN_SECRET, and
# neither exists in the deployed environment otherwise.
load_dotenv(Path(__file__).resolve().parent / ".env")

from .tools.deck import build_solution_deck, lookup_deck, update_deck

root_agent = LlmAgent(
    name="solutioning_agent",
    model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    instruction="""
You build first-draft solution decks.

When asked to build a deck for a client, first call lookup_deck with that
client name. If it returns found=true, a deck already exists — use
update_deck to change it rather than making a duplicate. Only call
build_solution_deck when lookup_deck returns found=false.

To edit a deck, you need the exact text on the slide to replace — ask the
person what to change it to if they haven't said, rather than guessing at
wording that isn't there.

Do not invent research, evidence, or commercial terms — none of that exists
yet. If asked for anything beyond producing or editing a placeholder deck,
say plainly that deck research and content generation aren't built yet.
""",
    tools=[build_solution_deck, lookup_deck, update_deck],
)
