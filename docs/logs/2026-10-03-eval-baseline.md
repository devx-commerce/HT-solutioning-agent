# 2026-10-03: ADK eval baseline, and the bugs it found

First runs of the two ADK eval sets in `evals/` (see `evals/README.md`).
Everything ran locally against the `solutioning_agent_eval` sandbox; no
production data was read or written.

## Final scores (all fixes below applied)

**briefs**: 9 real HT briefs, 9 of 9 passing.

| Case | Response rubrics | Research rubrics | Hallucination | Deck gate | No competitor | Names |
|---|---|---|---|---|---|---|
| Stanley Furniture | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Eli Lilly | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Rocksport | 1.0 | 1.0 | 0.98 | 1.0 | 1.0 | 1.0 |
| Pentonic | 1.0 | 1.0 | 0.96 | 1.0 | 1.0 | 1.0 |
| Lulu Mall | 0.93 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| KRBL Grains of Hope | 1.0 | 1.0 | 0.96 | 1.0 | 1.0 | 1.0 |
| Air India | 0.93 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| Uber | 1.0 | 1.0 | 0.98 | 1.0 | 1.0 | 1.0 |
| Perfetti Confetti | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |

The two rubrics still missed: Air India's reply didn't mention that some
past-deck searches and one page fetch returned nothing; Lulu Mall's native
articles had no named topic.

**refinement**: 6 scripted revision chats, 6 of 6 passing with every
rubric met (two decks for one client; delete, move and insert in one
request; picture swap; an upload refused for size and shape; an upload
placed; a table row and a heading edited).

These are single runs. Outputs vary between runs (Pentonic passed a rubric
in one run and missed it in the next), so treat a single run as indicative
and use `evals/run.sh briefs "" 3` before drawing conclusions.

## Bugs found and fixed

1. **A model timeout was never retried.** google-genai retries HTTP error
   codes and dropped connections, not a timeout, so the first timeout ended
   the whole brief with no deck (Lulu Mall and Rocksport in the first
   baseline). `_RegionalGemini.generate_content_async` now retries a
   timed-out call up to three times, unless output had already arrived.
2. **No request timeout at all.** One model call hung 8 minutes before the
   server reset the connection. Calls now time out at 6 minutes (deck
   writing usually takes 1 to 2, once took over 4) and are retried by 1.
3. **A placeholder's own prefix counted against its length limit.**
   `enforce` checked an image description's 140 characters before adding
   "Image placeholder (16:9): ", so a deck passed at build and failed every
   later revision, naming slides nobody had touched; the agent then edited
   those slides too. Hits production whenever image generation fails. The
   prefix is no longer measured.
4. **The agent fetched a URL it had guessed** (uber.com/in/en/, against
   the instruction). `fetch_url` now refuses any site that hasn't appeared
   in the email or an earlier tool result in the conversation, and a
   guessed `client_website` gets a placeholder logo instead of a lookup.
5. **Assumed figures went unmarked** in 3 of 9 cases ("25 colleges",
   "1,500 schools", "6 radio spots a day"). The instruction now spells out
   the convention ("(indicative)" next to each, or once in a table header)
   and asks for a check of every number before building. All three passed
   afterwards.
6. **`adk deploy agent_engine` would have shipped eval results.** ADK
   2.9.2's default exclusion is `set('.adk/')`, a set of single characters,
   so `.adk/` (eval results, including client emails) was never excluded.
   `agents/solutioning_agent/.ae_ignore` now excludes it; a test pins it.
7. **Eval-side**: the custom metrics read their threshold from the wrong
   field (ADK passes it in the criterion), and three rubrics were worded
   stricter than the policy they check (exact URL vs same site; a rejected
   edit that was then fixed counted as a "trial edit"). Fixed.

## Notes for running evals

- One run of all 9 briefs takes 45 to 60 minutes, mostly the judge
  (`gemini-2.5-pro`, 3 samples per rubric set).
- Don't edit `evals/run.sh` while it runs: bash reads the script as it
  goes, and the run ends with a spurious error.
- Don't run the two sets at once: both reset the same sandbox.
