-- Weekly report: what the Solutioning Agent did over a period, from the data
-- it already logs. Paste into the BigQuery console (project
-- academic-diode-477405-m3) and run; each query below shows as its own result.
-- Change the two dates to report on another period.

DECLARE period_start TIMESTAMP DEFAULT TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
DECLARE period_end   TIMESTAMP DEFAULT CURRENT_TIMESTAMP();

-- 1. Emails the sweep looked at, and what it decided about each.
SELECT decision, COUNT(*) AS emails
FROM solutioning_agent.decisions
WHERE recorded_at BETWEEN period_start AND period_end
GROUP BY decision
ORDER BY emails DESC;

-- 2. Briefs from inboxes: queued, built and failed. Each built brief also
--    sent one "Solution deck drafted" email to the inbox it came from.
SELECT status, triggered_by, COUNT(*) AS briefs
FROM solutioning_agent.ingestion_threads
WHERE created_at BETWEEN period_start AND period_end
GROUP BY status, triggered_by
ORDER BY status, triggered_by;

-- 3. Time to first draft (SOW target: within 4 working hours for 70% of
--    requests). From the email arriving to its deck being built. Wall-clock
--    hours; briefs that arrived outside working hours count from arrival.
SELECT
  COUNT(*) AS built_briefs,
  ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(updated_at, received_at, MINUTE), 100)[OFFSET(50)] / 60, 1) AS median_hours,
  ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(updated_at, received_at, MINUTE), 100)[OFFSET(70)] / 60, 1) AS p70_hours,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(TIMESTAMP_DIFF(updated_at, received_at, MINUTE) <= 240), COUNT(*)), 1) AS pct_within_4_hours
FROM solutioning_agent.ingestion_threads
WHERE status = 'built' AND received_at IS NOT NULL
  AND created_at BETWEEN period_start AND period_end;

-- 4. Decks: built new versus revised afterwards (in chat or by a rebuild).
SELECT
  COUNTIF(created_at BETWEEN period_start AND period_end) AS decks_built,
  COUNTIF(updated_at BETWEEN period_start AND period_end
          AND TIMESTAMP_DIFF(updated_at, created_at, MINUTE) > 5) AS decks_revised
FROM solutioning_agent.briefs
WHERE deck_file_id IS NOT NULL;

-- 5. Research sources: how often each returned something, nothing, or failed.
SELECT actor AS source,
       COUNTIF(outcome = 'success') AS returned_results,
       COUNTIF(outcome = 'no_results') AS returned_nothing,
       COUNTIF(outcome = 'error') AS failed,
       COUNT(*) AS calls
FROM solutioning_agent.audit_log
WHERE event_type = 'retrieval' AND recorded_at BETWEEN period_start AND period_end
GROUP BY source
ORDER BY calls DESC;

-- 6. Evidentiary grounding and retrieval transparency (SOW targets: 100%).
--    A draft is grounded when at least one research source returned a cited
--    link for it; it is transparent when its sources were logged at all
--    (the notification's sources section is built from these logs).
WITH drafts AS (
  SELECT brief_id FROM solutioning_agent.briefs
  WHERE deck_file_id IS NOT NULL AND created_at BETWEEN period_start AND period_end
),
evidence AS (
  SELECT brief_id,
         COUNT(*) AS retrieval_calls,
         COUNTIF(outcome = 'success'
                 AND ARRAY_LENGTH(JSON_VALUE_ARRAY(detail, '$.source_urls')) > 0) AS cited_results
  FROM solutioning_agent.audit_log
  WHERE event_type = 'retrieval'
  GROUP BY brief_id
)
SELECT
  COUNT(*) AS drafts,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(IFNULL(e.cited_results, 0) > 0), COUNT(*)), 1) AS pct_with_a_cited_source,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(IFNULL(e.retrieval_calls, 0) > 0), COUNT(*)), 1) AS pct_with_sources_logged
FROM drafts d LEFT JOIN evidence e USING (brief_id);

-- 7. People: inboxes connected, and any that need reconnecting.
SELECT status, COUNT(*) AS inboxes
FROM solutioning_agent.users
GROUP BY status;
