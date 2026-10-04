-- Weekly report: what the Solutioning Agent did over a period, from the data
-- it already logs. Paste into the BigQuery console (project
-- academic-diode-477405-m3) and run; each query below shows as its own result.
-- Change the two dates to report on another period. The weekly email
-- (evals/report.py) reads these queries in this order.

DECLARE period_start TIMESTAMP DEFAULT TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY);
DECLARE period_end   TIMESTAMP DEFAULT CURRENT_TIMESTAMP();

-- 1. Emails checked, by decision
--    Every email the inbox check looked at, and what it decided about it.
SELECT CASE decision
         WHEN 'solution_request' THEN 'A brief'
         WHEN 'not_a_request' THEN 'Not a brief'
         WHEN 'thread_already_active' THEN 'Thread already handled'
         WHEN 'manual_flag_queued' THEN 'Labelled generate-deck'
         WHEN 'manual_flag_insufficient' THEN 'Labelled, but no usable content'
         ELSE decision END AS decision,
       COUNT(*) AS emails
FROM solutioning_agent.decisions
WHERE recorded_at BETWEEN period_start AND period_end
GROUP BY decision
ORDER BY emails DESC;

-- 2. Briefs from email, by outcome
--    Queued, built and failed. Each built brief also sent one "Solution deck
--    drafted" email to the inbox it came from.
SELECT CASE status WHEN 'built' THEN 'Built' WHEN 'failed' THEN 'Failed' WHEN 'building' THEN 'In progress' ELSE status END AS outcome,
       CASE triggered_by WHEN 'branch_a' THEN 'Automatically' WHEN 'branch_b' THEN 'generate-deck label' ELSE triggered_by END AS picked_up,
       COUNT(*) AS briefs
FROM solutioning_agent.ingestion_threads
WHERE created_at BETWEEN period_start AND period_end
GROUP BY outcome, picked_up
ORDER BY outcome, picked_up;

-- 3. Time to first draft (target: within 4 hours for 70% of briefs)
--    From the email arriving to its deck being built. Wall-clock hours;
--    briefs that arrived outside working hours count from arrival.
SELECT
  COUNT(*) AS briefs_built,
  ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(updated_at, received_at, MINUTE), 100)[OFFSET(50)] / 60, 1) AS median_hours,
  ROUND(APPROX_QUANTILES(TIMESTAMP_DIFF(updated_at, received_at, MINUTE), 100)[OFFSET(70)] / 60, 1) AS hours_for_70_percent,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(TIMESTAMP_DIFF(updated_at, received_at, MINUTE) <= 240), COUNT(*)), 1) AS percent_within_4_hours
FROM solutioning_agent.ingestion_threads
WHERE status = 'built' AND received_at IS NOT NULL
  AND created_at BETWEEN period_start AND period_end;

-- 4. Decks built and revised
--    New decks, and decks revised afterwards (in chat or by a rebuild).
SELECT
  COUNTIF(created_at BETWEEN period_start AND period_end) AS decks_built,
  COUNTIF(updated_at BETWEEN period_start AND period_end
          AND TIMESTAMP_DIFF(updated_at, created_at, MINUTE) > 5) AS decks_revised
FROM solutioning_agent.briefs
WHERE deck_file_id IS NOT NULL;

-- 5. Research sources
--    How often each returned something, returned nothing, or failed.
SELECT CASE actor WHEN 'past_decks' THEN 'HT past decks' WHEN 'web_search' THEN 'Web and social' WHEN 'youtube' THEN 'YouTube' WHEN 'fetch_url' THEN 'Web pages' ELSE actor END AS source,
       COUNTIF(outcome = 'success') AS returned_results,
       COUNTIF(outcome = 'no_results') AS returned_nothing,
       COUNTIF(outcome = 'error') AS failed,
       COUNT(*) AS calls
FROM solutioning_agent.audit_log
WHERE event_type = 'retrieval' AND recorded_at BETWEEN period_start AND period_end
GROUP BY source
ORDER BY calls DESC;

-- 6. Grounding and transparency (target: 100% each)
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
  ROUND(100 * SAFE_DIVIDE(COUNTIF(IFNULL(e.cited_results, 0) > 0), COUNT(*)), 1) AS percent_with_a_cited_source,
  ROUND(100 * SAFE_DIVIDE(COUNTIF(IFNULL(e.retrieval_calls, 0) > 0), COUNT(*)), 1) AS percent_with_sources_logged
FROM drafts d LEFT JOIN evidence e USING (brief_id);

-- 7. Connected inboxes
--    Inboxes connected, and any that need reconnecting.
SELECT CASE status WHEN 'active' THEN 'Connected' WHEN 'reauthorization_required' THEN 'Needs reconnecting' ELSE status END AS status,
       COUNT(*) AS inboxes
FROM solutioning_agent.users
GROUP BY status;
