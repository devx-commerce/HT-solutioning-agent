# Evals

ADK's own eval framework (`adk eval`), two eval sets:

- **briefs**: real briefs HT's sales team sent, each scored by an LLM judge
  against rubrics and by three code checks;
- **refinement**: scripted chats that revise seeded decks (add, delete and
  move slides, swap a picture, attach an image that fits and one that
  doesn't, two decks for one client), judged on exactly what the agent did.

## What's here

| File | What it is |
|---|---|
| `refinement_cases.json` | The revision chats, the decks they start from (seeded from `fixtures/pentonic_deck.json`), and their rubrics. |
| `refinement.evalset.json`, `refinement_config.json` | The generated refinement eval set and its metrics (every rubric must hold). |
| `fixtures/` | The seed deck and the two upload images. |
| `cases.json` | The cases: each real brief's Gmail thread in `sales.agent@`'s inbox, the HT deck that was actually sent (for reference, not in the repo), that deck's Drive id if it's in the past-decks folder, and the case's rubrics. **Edit rubrics here.** |
| `build_evalsets.py` | Turns `cases.json` and the emails into `briefs.evalset.json`. Run after adding a case or editing a rubric. |
| `briefs.evalset.json` | The ADK eval set. Generated; committed. |
| `test_config.json` | Which metrics run, their thresholds, the judge model, and the rubrics that apply to every case. |
| `metrics.py` | The three code checks, registered in `test_config.json` as ADK custom metrics. |
| `run.sh` | Runs the eval set against this checkout's agent, in the sandbox. |
| `reset_sandbox.py` | Empties the sandbox's `briefs` table between runs and, for refinement, re-seeds the starting decks (it can only touch the eval dataset and folder). |

## Running it

```bash
pip install -r requirements-dev.txt        # once: ADK's eval extras
gcloud auth application-default login      # an account that can read the agent's secrets
evals/run.sh                               # the real briefs, every case once (about 5 min a case)
evals/run.sh briefs pentonic,uber          # just these
evals/run.sh briefs "" 3                   # three times, to see how much it varies
evals/run.sh refinement                    # the revision chats
```

The agent runs in this process with its real tools and models, pointed at a
sandbox: the `solutioning_agent_eval` BigQuery dataset, the "Eval Decks"
Drive folder (inside "Agent Generated Decks"), and no sharing. Production
data is never read or written. Decks are rendered by a local renderer
(`renderer/dist`, needs Node) unless `RENDER_URL` points at a deployed one.

## What's measured

| Metric | Kind | Passes when |
|---|---|---|
| `rubric_based_final_response_quality_v1` | judge | ≥ 75% of the case's rubrics and the global ones hold, judged on the reply and the full deck the agent built |
| `rubric_based_tool_use_quality_v1` | judge | ≥ 75% of the research-habit rubrics hold (past decks from several angles, competitors checked, no guessed URLs, rejections fixed) |
| `hallucinations_v1` | judge | ≥ 80% of the reply's statements are supported by what the tools returned |
| `deck_quality_gate` | code | a deck was published with HT's logo and at least 3 generated images |
| `no_competitor_sources` | code | no competitor publication's link in anything the agent cites |
| `component_names_consistent` | code | ≥ 75% of the overview slide's cards reappear word for word as a later slide's eyebrow |

The judge is `gemini-2.5-pro` (the strongest model this project can reach,
and not the one that writes the decks), sampled 3 times per rubric with a
majority vote.

**Rubrics describe what a good deck must achieve, not HT's answer.** Where
HT's own deck suggests a need ("a way to measure print ROI"), the rubric
names HT's choice only as an example, so a deck that solves it differently,
or better, still passes. In three cases HT's own deck breaks the brief (a
jacket for Stanley despite "no innovations", for one); the rubrics follow
the brief.

**Each case hides its own answer.** A case lists the Drive id of HT's deck
for that brief, and `search_past_decks` skips it for that case only (session
state key `eval_hidden_past_decks`); every other past deck stays searchable.

## Reading results

`--print_detailed_results` prints every rubric verdict with the judge's
reasoning. Results are also saved under `agents/solutioning_agent/.adk/`
and show in the Eval tab of `adk web agents`. Look at the reasoning before
trusting a score: a failure is either a real gap in the deck or a rubric
worded too tightly, and the second is fixed in `cases.json`.

## Adding a case

1. Make sure the brief's email is in `sales.agent@`'s inbox.
2. Add it to `cases.json`: its thread id, its HT deck's Drive id if that deck
   is in the past-decks folder, and 6 to 10 rubrics written as needs.
3. `python -m evals.build_evalsets briefs`, and commit both files.

A refinement case goes in `refinement_cases.json` (give it its own seed deck,
since cases run in parallel), then `python -m evals.build_evalsets refinement`.

Only briefs HT actually received belong here. The Tata Sampann, Akasa Air
and Rapido briefs used in demos were written for the pilot and are excluded.
