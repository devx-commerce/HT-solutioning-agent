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

Pictures now scale with the deck: one for every two to three content slides,
capped by `settings.max_images`. The cap of 13 keeps pictures under ₹50 a
deck (13 × ₹3.70 = ₹48); a 30-slide deck has about 10 to 13. Raise the cap
only with that cost in mind. A revision that adds pictures counts toward the
same cap, so a deck never holds more than 13.
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

## Exact costs: the billing export

The proper record, with nothing counted by hand: Google's billing export to
BigQuery has every charge, SKU by SKU, with the labels above attached.

Turning it on, once: a billing account administrator (Billing Account
Administrator on HTDS-DirectBilling) opens Billing > Billing export >
BigQuery export > **Detailed usage cost** > Edit settings, chooses project
`academic-diode-477405-m3` and a new dataset `billing_export` (US). It fills
from that day on, a few hours behind; nothing earlier is backfilled. The
table is `billing_export.gcp_billing_export_resource_v1_0127C2_94642F_791C48`.

This app's cost per day, by component and live or eval, credits included:

```sql
SELECT
  DATE(usage_start_time, "Asia/Kolkata") AS day,
  service.description AS service,
  (SELECT value FROM UNNEST(labels) WHERE key = "component") AS component,
  (SELECT value FROM UNNEST(labels) WHERE key = "run") AS run,
  ROUND(SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)), 2) AS inr
FROM `academic-diode-477405-m3.billing_export.gcp_billing_export_resource_v1_0127C2_94642F_791C48`
WHERE project.id = "academic-diode-477405-m3"
  AND EXISTS (SELECT 1 FROM UNNEST(labels) WHERE key = "app" AND value = "solutioning-agent")
  AND usage_start_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
GROUP BY day, service, component, run
ORDER BY day DESC, inr DESC;
```

Live cost per deck built, by day:

```sql
WITH live AS (
  SELECT DATE(usage_start_time, "Asia/Kolkata") AS day,
         SUM(cost) + SUM(IFNULL((SELECT SUM(c.amount) FROM UNNEST(credits) c), 0)) AS inr
  FROM `academic-diode-477405-m3.billing_export.gcp_billing_export_resource_v1_0127C2_94642F_791C48`
  WHERE EXISTS (SELECT 1 FROM UNNEST(labels) WHERE key = "app" AND value = "solutioning-agent")
    AND EXISTS (SELECT 1 FROM UNNEST(labels) WHERE key = "run" AND value = "live")
  GROUP BY day
),
decks AS (
  SELECT DATE(created_at, "Asia/Kolkata") AS day, COUNT(*) AS decks
  FROM `academic-diode-477405-m3.solutioning_agent.briefs`
  WHERE deck_file_id IS NOT NULL
  GROUP BY day
)
SELECT day, decks, ROUND(inr, 2) AS inr, ROUND(SAFE_DIVIDE(inr, decks), 2) AS inr_per_deck
FROM live JOIN decks USING (day)
ORDER BY day DESC;
```

The same table also feeds the Looker Studio billing template (Billing >
Reports > Visualise in Looker Studio) if a dashboard is wanted; filter it on
`app = solutioning-agent`. Keep both out of HT's docs and the weekly email,
which goes to an HT inbox.

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
