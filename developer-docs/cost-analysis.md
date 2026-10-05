# Cost analysis

Internal. Not for HT's docs. Measured 5 Oct 2026, at October 2026 list prices
in INR (Cloud Billing catalog).

## Summary

| Item | Roughly |
|---|---|
| One real deck from a brief | ₹50 |
| One eval case (a deck, plus the judge scoring it) | ₹150 |
| Smoke eval run (2 cases) | ₹300 |
| Revision chats eval (6 chats) | ₹180 |
| Full eval run (9 briefs × 3, plus the revision chats) | ₹4,100 |
| 100 live decks a month | ₹5,000 |

Evals run only when started by hand (Cloud Build > Triggers > Run on
`solutioning-agent-weekly-evals`). The weekly schedule and the after-deploy
smoke run were removed on 5 Oct 2026 because of this cost.

## A real deck, about ₹50

| Part | Model | Per deck | Cost |
|---|---|---|---|
| The agent researching and writing | gemini-3.8-flash | ~10 calls, ~129k tokens in, ~22k out | ~₹34 |
| Pictures | gemini-2.5-flash-image | ~4 images, ~1.3k output tokens each | ~₹16 (₹3.70 an image) |
| Is this a brief? | gemini-2.5-flash-lite | 1 call | under ₹0.10 |
| Web research grounding | Google Search on Gemini 3 | ~5 searches | free (first 5,000 a month) |

Where the agent's tokens go:

- Every call re-sends the fixed instruction (~4.8k tokens) and the ten tool
  declarations (~2.9k tokens): ~77k of the ~129k input tokens per deck, about
  ₹11. Gemini's implicit caching may discount this; the token metrics don't
  show cached tokens separately, so check the "cached" SKUs in Billing
  Reports before counting on it.
- The rest of the input is research results (~45k characters a deck), the
  brief and the growing conversation.
- Output: the deck JSON is ~3k tokens; most of the ~22k is the model's
  thinking (~₹13).

## An eval case, about ₹150

The same ₹50 deck, plus ~₹96 of judging by gemini-2.5-pro:

- 8 judge calls a case: 3 samples for response quality, 3 for research
  quality, 2 for grounding (`num_samples` in `evals/test_config.json`).
- Each reads ~30k tokens (brief, every research result, the deck, the
  rubrics) and writes ~8.8k, of which ~7k is thinking. Output costs ₹960 a
  million, 8 times the input rate, so ~₹68 of the ₹96 is the judge thinking.
- A full run repeats every case 3 times, so each rubric is judged 9 times a
  brief.

## Cutting eval cost

None of these are applied.

| Change | Full run | Trade-off |
|---|---|---|
| As now | ~₹4,100 | |
| Judge samples once (`num_samples: 1`) | ~₹2,800 | Noisier single verdicts; the 3 repeats average them anyway |
| Cap the judge's thinking (`judge_model_config.thinking_config.thinking_budget`, e.g. 2048) | ~₹2,400 | Check verdicts match the uncapped run first |
| Each brief once (`run.sh briefs "" 1` in `deploy/cloudbuild-evals.yaml`) | ~₹1,500 | No view of run-to-run variation |
| All three | ~₹700 | |

A cheaper judge model barely helps: gemini-3.8-flash output is only ~25%
cheaper than gemini-2.5-pro's.

## Seeing the cost in Cloud Billing

The project also hosts about ten other teams' Agent Engines, so its total is
not this app's cost. This app labels its usage:

| Label | Values | On |
|---|---|---|
| `app` | `solutioning-agent` | Every resource, every Gemini call, every BigQuery query |
| `component` | `agent`, `research`, `images`, `classifier`, `eval-judge`, `pipeline` | Gemini calls and BigQuery queries |
| `run` | `live`, `eval` | Gemini calls and BigQuery queries (`SOLUTIONING_RUN=eval`, set by `evals/run.sh`) |

Not labelled: Cloud Build, the past-decks search (Vertex AI Search), Gemini
Enterprise seats (a subscription). Labels on Gemini calls apply from the 5 Oct
2026 deploy; earlier usage is unlabelled.

Quick view: Billing > Reports, Projects = HT-GoogleAgentSpace, Labels `app` =
`solutioning-agent`, group by Service or by label `component` / `run`. Needs
Billing Account Viewer; data lags up to a day.

Standing dashboard: a billing admin turns on Billing > Billing export >
BigQuery export > Detailed usage cost (into e.g. `billing_export`); then the
Looker Studio billing template, filtered on `app = solutioning-agent`. Charts
worth having: cost per day by `component`, live against eval, cost per deck
(daily live cost over decks built that day from `solutioning_agent.briefs`).

## How these were measured

Token and call counts come from Cloud Monitoring
(`aiplatform.googleapis.com/publisher/online_serving/token_count` and
`model_invocation_count`, by `model_user_id`) over the window of a single full
pass of the nine briefs (2 Oct 2026, 22:46 to 23:45 UTC) and of the revision
chats (22:37 to 22:45 UTC). The 72 gemini-2.5-pro calls in the first window
are exactly 8 per case, so no other team's usage was mixed in. Prices are from
the Cloud Billing catalog API (Vertex AI service `C7E2-9256-1C43`,
`currencyCode=INR`):

| SKU | ₹ per million |
|---|---|
| Gemini 3.8 Flash Global Text Input | 143.98 |
| Gemini 3.8 Flash Global Text Output | 719.91 |
| Gemini 2.5 Pro Text Input (≤200k) | 119.98 |
| Gemini 2.5 Pro Text and Thinking Output (≤200k) | 959.89 |
| Gemini 2.5 Flash Image Output | 2,879.66 |
| Grounding with Google Search on Gemini 3 | free to 5,000 a month, then ₹1.34 a search |

| Window | Model | Tokens in | Tokens out | Calls |
|---|---|---|---|---|
| 9 briefs, one pass | gemini-3.8-flash | 1,162,870 | 198,148 | 94 |
| | gemini-2.5-pro | 2,140,843 | 633,582 | 72 |
| | gemini-2.5-flash-image | 3,362 | 50,310 | 39 |
| 6 revision chats | gemini-3.8-flash | 210,332 | 9,381 | |
| | gemini-2.5-pro | 419,861 | 84,356 | |
| | gemini-2.5-flash-image | 173 | 2,580 | |

To repeat: run any eval or deck build alone (no other build in Cloud Build at
the same time), then sum those two metrics over its window.
