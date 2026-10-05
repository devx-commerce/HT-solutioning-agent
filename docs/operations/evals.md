# Evals

Evals measure the quality of the agent's work on real briefs, using Google's
Agent Development Kit evaluation framework (`adk eval`). They answer: when a
real brief comes in, does the deck address what was asked, is its research
sound, and is it built properly?

## When they run

| Run | What | How long | Where the result goes |
|---|---|---|---|
| After every deploy | The smoke cases (`settings.evals.smoke_cases`), once | About 15 minutes | Emailed to `settings.eval_summary_email`; kept in the eval bucket |
| Weekly | Every case three times, plus the revision chats | About 3 to 4 hours | Same, together with the week's usage report ([weekly-report.md](weekly-report.md)) |
| By hand | Any cases you choose | About 5 minutes a case | Printed, and kept locally |

A failing eval never blocks a deploy. Read the summary, and treat a drop in
scores as a signal to look at that case.

For the same reason, the `evals` step in Cloud Build always shows as
successful. The result is in the summary email and at the end of that step's
log ("Evals: N of M cases passed").

## What's evaluated

**Nine real briefs HT's sales team received** (Stanley Furniture, Eli Lilly,
Rocksport, Pentonic, Lulu Mall, KRBL, Air India, Uber, Perfetti), each sent to
the agent exactly as the email pipeline would send it. Each has 8 to 10
rubrics: specific, checkable statements about what a good deck for that
brief must do, written from the brief and from what HT's own team proposed.

Rubrics describe the outcome, not HT's exact answer. Where HT's deck suggests
a need, HT's choice is named only as an example, so a deck that meets the
need another way, or better, still passes.

**Six revision chats** against prepared decks: two decks for one client;
adding, deleting and moving slides in one request; a picture swap; an
uploaded image that doesn't fit and one that does; editing a table and a
heading.

## The scores

| Score | Checked by | Passes when |
|---|---|---|
| Response quality | Judge model | At least 75% of the case's rubrics and the general ones hold, judged on the email and the full deck |
| Research quality | Judge model | At least 75% of the research rubrics hold (past decks searched from several angles, competitors checked, no guessed websites, rejected drafts fixed) |
| Grounding | Judge model | At least 80% of the email's statements are supported by what research returned |
| Deck built properly | Code | A deck was published with HT's logo and at least `min_images` pictures |
| No competitor sources | Code | No competitor newspaper is cited anywhere |
| Consistent names | Code | Each part of the solution keeps one name from the overview to its own slide |

The judge model is `gemini-2.5-pro`, a stronger model than the one that
writes the decks, sampled three times per rubric set with a majority vote.

Each case hides its own HT deck from the past-decks search, so the agent
can't copy the answer it's scored against. All other past decks stay
searchable.

## Running evals by hand

From a checkout, signed in to Google Cloud:

```bash
pip install -r requirements-dev.txt
evals/run.sh                               # every brief, once
evals/run.sh briefs pentonic,uber          # just these
evals/run.sh briefs "" 3                   # three times, to see how much results vary
evals/run.sh refinement                    # the revision chats
```

The agent runs with its real tools and models against a separate sandbox
(the `solutioning_agent_eval` dataset and the "Eval Decks" folder), so evals
never read or change real data.

Results vary from run to run, because the agent's research and writing vary.
One run is indicative; three runs show the real picture.

## Adding a case

1. Make sure the brief's email is in sales.agent@hindustantimes.com's inbox.
2. Add it to `evals/cases.json`: its Gmail thread id, the Drive id of HT's
   own deck for it if that deck is in the past decks folder, and 6 to 10
   rubrics written as needs.
3. Run `python -m evals.build_evalsets briefs` and commit both files.

Only briefs HT actually received belong in the eval set.
