"""Labels that let Cloud Billing show this app's cost on its own.

The project also hosts other teams' agents, so model calls and queries carry
app=solutioning-agent plus which part made them, and whether it was live use
or an eval run (SOLUTIONING_RUN=eval, set by evals/run.sh).
"""

from __future__ import annotations

import os

from google.cloud import bigquery


def labels(component: str) -> dict[str, str]:
    return {"app": "solutioning-agent", "component": component,
            "run": os.environ.get("SOLUTIONING_RUN", "live")}


def query_config() -> bigquery.QueryJobConfig:
    """Default config for a BigQuery client; per-query settings are merged in."""
    return bigquery.QueryJobConfig(labels=labels("agent"))
