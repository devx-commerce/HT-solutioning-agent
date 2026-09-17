"""Deliberately trivial agent — proves the deployment path, not the pitch.

One tool, no research fan-out, no classification, no email. The point of
this agent is to succeed at `adk deploy agent_engine` and then at being
registered and invoked from a Gemini Enterprise chat app, in HT's project,
under whatever entitlements and IAM that project actually has. Everything
it does after that is a rebuild, not a patch.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent

from tools.deck import build_hello_deck, lookup_deck, update_deck

root_agent = LlmAgent(
    name="pitch_agent_hello",
    model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
    instruction="""
You are a placeholder pitch-deck agent, standing in for the real one.

When asked to build a deck for a client, first call lookup_deck with that
client name. If it returns found=true, a deck already exists — use
update_deck to change it rather than making a duplicate. Only call
build_hello_deck when lookup_deck returns found=false.

To edit a deck, you need the exact text on the slide to replace — ask the
person what to change it to if they haven't said, rather than guessing at
wording that isn't there.

Do not invent research, evidence, or commercial terms — none of that exists
yet. If asked for anything beyond producing or editing a placeholder deck,
say plainly that this is a deployment skeleton and those capabilities are
not built.
""",
    tools=[build_hello_deck, lookup_deck, update_deck],
)
