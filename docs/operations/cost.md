# What it costs

The project `academic-diode-477405-m3` also hosts other teams' agents, so its
total bill is not this app's cost. Everything this app runs or calls is
labelled so Cloud Billing can separate it.

## The labels

| Label | Values | On |
|---|---|---|
| `app` | `solutioning-agent` | Every resource (Cloud Run services, Pub/Sub, BigQuery datasets, the eval bucket, secrets, the Agent Engine), every Gemini call and every BigQuery query |
| `component` | `agent`, `research`, `images`, `classifier`, `eval-judge`, `pipeline` | Gemini calls and BigQuery queries: which part of the app made them |
| `run` | `live`, `eval` | Gemini calls and BigQuery queries: real use, or an eval run |

Gemini calls are most of the cost, so `component` and `run` are what answer
"what does a deck cost" and "what do evals cost".

Not labelled: Cloud Build (deploys and the weekly eval run, a few hundred
rupees a month), the past-decks search (Vertex AI Search), and Gemini
Enterprise seats, which are a subscription rather than usage.

## Seeing it

You need **Billing Account Viewer** on the billing account.

**Quick view.** Billing > Reports. Set Projects to HT-GoogleAgentSpace,
Labels to `app` = `solutioning-agent`, and Group by Service, or by label
`component` or `run`. Billing data runs up to a day behind.

**A standing dashboard.** Once, a billing account administrator turns on
Billing > Billing export > BigQuery export > **Detailed usage cost**, into a
dataset such as `billing_export` in this project. Data starts from that day.
Then open the Cloud Billing report template in Looker Studio (Billing >
Reports > Visualise in Looker Studio), point it at the export table, and
filter on the label `app = solutioning-agent`. Useful charts: cost per day by
`component`, live against eval spend, and cost per deck (daily live cost
divided by decks built that day, from `solutioning_agent.briefs`).

## Typical figures

At October 2026 list prices: a deck from a brief costs roughly ₹50 in model
calls, and the weekly eval run roughly ₹4,100 (see [evals.md](evals.md)).
Google Search grounding is free for the first 5,000 searches a month.
