"""BigQuery-backed state for the ingestion sweep.

Three concerns, three tables: the watermark (sweep_state), message-level
idempotency and audit (decisions), thread-level intake tracking
(ingestion_threads — deliberately separate from whatever the agent's own
tools record in `briefs` about a built deck). See
developer-docs/EMAIL-POLLER-DESIGN.md for why BigQuery, including the honest tradeoff
against a point-lookup-shaped store like Firestore.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from google.cloud import bigquery

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
DATASET = os.environ.get("BQ_DATASET", "solutioning_agent")

BOOTSTRAP_LOOKBACK = timedelta(hours=24)
WATERMARK_OVERLAP = timedelta(minutes=5)


def _client() -> bigquery.Client:
    return bigquery.Client(project=PROJECT)


# --- watermark --------------------------------------------------------------


def bootstrap_cutoff() -> datetime:
    """The 'as if this were the first run' cutoff — also what force=True
    uses, deliberately, so a test sweep never depends on the real
    watermark's history."""
    return datetime.now(timezone.utc) - BOOTSTRAP_LOOKBACK


def get_sweep_cutoff() -> datetime:
    """The Branch A `after:` cutoff — last successful sweep, minus overlap,
    or a 24h bootstrap default on the very first run."""
    rows = list(
        _client()
        .query(
            f"SELECT last_swept_at FROM `{PROJECT}.{DATASET}.sweep_state` "
            f"WHERE id = 'default'"
        )
        .result()
    )
    if not rows or rows[0]["last_swept_at"] is None:
        return bootstrap_cutoff()
    return rows[0]["last_swept_at"] - WATERMARK_OVERLAP


def set_sweep_watermark(swept_at: datetime) -> None:
    _client().query(
        f"""
        MERGE `{PROJECT}.{DATASET}.sweep_state` T
        USING (SELECT 'default' AS id) S ON T.id = S.id
        WHEN MATCHED THEN UPDATE SET last_swept_at = @swept_at
        WHEN NOT MATCHED THEN INSERT (id, last_swept_at) VALUES ('default', @swept_at)
        """,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("swept_at", "TIMESTAMP", swept_at.isoformat())
            ]
        ),
    ).result()


# --- message-level idempotency + audit --------------------------------------


def decision_exists(message_id: str) -> bool:
    rows = list(
        _client()
        .query(
            f"SELECT 1 FROM `{PROJECT}.{DATASET}.decisions` WHERE message_id = @mid LIMIT 1",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("mid", "STRING", message_id)]
            ),
        )
        .result()
    )
    return len(rows) > 0


def record_decision(
    message_id: str,
    decision: str,
    reason: str,
    confidence: float | None = None,
    brief_id: str | None = None,
) -> None:
    _client().insert_rows_json(
        f"{PROJECT}.{DATASET}.decisions",
        [
            {
                "message_id": message_id,
                "brief_id": brief_id,
                "decision": decision,
                "reason": reason,
                "confidence": confidence,
                "recorded_at": datetime.now(timezone.utc).isoformat(),
            }
        ],
    )


# --- thread-level intake tracking -------------------------------------------


def thread_status(thread_id: str) -> dict | None:
    rows = list(
        _client()
        .query(
            f"SELECT status, sheet_row_written, brief_id FROM "
            f"`{PROJECT}.{DATASET}.ingestion_threads` WHERE thread_id = @tid LIMIT 1",
            job_config=bigquery.QueryJobConfig(
                query_parameters=[bigquery.ScalarQueryParameter("tid", "STRING", thread_id)]
            ),
        )
        .result()
    )
    return dict(rows[0]) if rows else None


def open_thread(thread_id: str, triggered_by: str) -> None:
    # MERGE, not a plain INSERT — this also re-opens a thread that already
    # has a row (a manual generate-deck override on an already-built
    # thread, or a fresh message retrying one marked failed), resetting it
    # to 'building' in place rather than creating a second row for the
    # same thread_id. See ingestion.py's thread-lock rules for when a
    # re-open is actually allowed to happen.
    #
    # DML (MERGE/INSERT), not insert_rows_json (streaming) — a row from the
    # streaming API sits in BigQuery's streaming buffer for up to ~90
    # minutes, during which UPDATE/DELETE on it is rejected outright. Every
    # thread this opens gets UPDATEd within seconds by mark_thread_built /
    # mark_thread_sheet_written, so streaming insert here always broke that
    # immediately. Confirmed live 2026-09-24. DML has no such buffer.
    now = datetime.now(timezone.utc).isoformat()
    _client().query(
        f"""
        MERGE `{PROJECT}.{DATASET}.ingestion_threads` T
        USING (SELECT @thread_id AS thread_id) S ON T.thread_id = S.thread_id
        WHEN MATCHED THEN UPDATE SET
          status = 'building',
          triggered_by = @triggered_by,
          sheet_row_written = FALSE,
          brief_id = NULL,
          updated_at = @now
        WHEN NOT MATCHED THEN
          INSERT (thread_id, status, triggered_by, sheet_row_written, brief_id, created_at, updated_at)
          VALUES (@thread_id, 'building', @triggered_by, FALSE, NULL, @now, @now)
        """,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("thread_id", "STRING", thread_id),
                bigquery.ScalarQueryParameter("triggered_by", "STRING", triggered_by),
                bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
            ]
        ),
    ).result()


def _update_thread(thread_id: str, **fields) -> None:
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = @{k}" for k in fields)
    params = [
        bigquery.ScalarQueryParameter(
            k, "BOOL" if isinstance(v, bool) else "STRING", v
        )
        for k, v in fields.items()
    ]
    params.append(bigquery.ScalarQueryParameter("tid", "STRING", thread_id))
    _client().query(
        f"UPDATE `{PROJECT}.{DATASET}.ingestion_threads` SET {set_clause} "
        f"WHERE thread_id = @tid",
        job_config=bigquery.QueryJobConfig(query_parameters=params),
    ).result()


def mark_thread_built(thread_id: str, brief_id: str) -> None:
    _update_thread(thread_id, status="built", brief_id=brief_id)


def mark_thread_sheet_written(thread_id: str) -> None:
    _update_thread(thread_id, sheet_row_written=True)


def retrieval_summary(brief_id: str) -> list[dict]:
    """What each research source actually returned for this brief.

    Read back from the telemetry the research tools wrote, not from the
    agent's own account of itself — the SOW requires every draft to state
    which sources returned results and which didn't, and a model
    summarising its own tool calls can get that wrong.
    """
    rows = _client().query(
        f"""
        SELECT actor AS source,
               -- One row per source, not per (source, outcome): a source
               -- searched several times read as both "returned results" and
               -- "returned nothing" on consecutive lines.
               CASE WHEN COUNTIF(outcome = 'success') > 0 THEN 'success'
                    WHEN COUNTIF(outcome = 'no_results') > 0 THEN 'no_results'
                    ELSE 'error' END AS outcome,
               COUNT(*) AS calls,
               COUNTIF(outcome = 'success') AS successes
        FROM `{PROJECT}.{DATASET}.audit_log`
        WHERE brief_id = @brief_id AND event_type = 'retrieval'
        GROUP BY source
        ORDER BY source
        """,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("brief_id", "STRING", brief_id)
            ]
        ),
    ).result()
    return [dict(r) for r in rows]


def retrieved_urls(brief_id: str) -> set[str]:
    """Every source url a research tool returned for this brief.

    The allow-list the delivery email's citations are checked against: a
    link the agent cites that no tool returned did not come from research.
    """
    rows = _client().query(
        f"""
        SELECT DISTINCT url
        FROM `{PROJECT}.{DATASET}.audit_log`,
             UNNEST(JSON_VALUE_ARRAY(detail, '$.source_urls')) AS url
        WHERE brief_id = @brief_id AND event_type = 'retrieval'
        """,
        job_config=bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("brief_id", "STRING", brief_id)
            ]
        ),
    ).result()
    return {r["url"] for r in rows}


def mark_thread_failed(thread_id: str, detail: str) -> None:
    _update_thread(thread_id, status="failed")
    record_decision(thread_id, "build_failed", detail)
