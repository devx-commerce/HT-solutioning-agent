-- The solutioning agent's tables, built out one piece at a time.
-- Run once via bq query, with --parameter substitution or a sed pass for
-- PROJECT/DATASET — see developer-docs/setup-and-deployment-notes.md for the exact command.

-- deck_json and research_brief are the system-of-record columns for the
-- presentation-md rendering pipeline and the research step (see
-- developer-docs/research-and-rendering-decisions.md). Both are JSON stored as STRING
-- rather than BigQuery JSON type, matching the loose/free-form shape decided
-- for research_brief and the presentation-md deck.schema.json shape for
-- deck_json. Both nullable — not every existing row will have them, and a
-- brief that hasn't been drafted/researched yet legitimately has neither.
CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.briefs` (
  brief_id STRING NOT NULL,
  message_id STRING,
  client_name STRING,
  status STRING,
  attempts INT64,
  detail STRING,
  deck_file_id STRING,
  deck_link STRING,
  deck_json STRING,
  research_brief STRING,
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.decisions` (
  message_id STRING NOT NULL,
  brief_id STRING,
  decision STRING,
  reason STRING,
  confidence FLOAT64,
  recorded_at TIMESTAMP
);

-- One row per onboarded account manager. Written by gmail_oauth.py when
-- someone visits /oauth/gmail/start and consents; read by gmail_client.py's
-- active_users() on every /sweep. gmail_secret is a Secret Manager resource
-- name, never the token itself — BigQuery holds no credential material.
CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.users` (
  email STRING NOT NULL,
  gmail_secret STRING NOT NULL,
  status STRING,  -- 'active' | 'reauthorization_required'
  onboarded_at TIMESTAMP,
  updated_at TIMESTAMP
);

-- Generic event log — merges what used to be three separate, unused tables
-- (handoffs, retrieval_log, a narrower audit_log) into one. Nothing writes
-- to this yet (notifications.py's sends aren't logged anywhere today, and
-- the retrieval layer doesn't exist yet either) — kept as one flexible
-- shape instead of guessing at three separate ones ahead of the code that
-- will actually populate it. event_type distinguishes what kind of event
-- this is ('tool_call' | 'retrieval' | 'handoff', extend as needed);
-- detail holds a JSON blob of whatever fields that event_type needs
-- (duration_ms, result_count, recipient/subject, ...) rather than a column
-- per possible field.
CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.audit_log` (
  brief_id STRING,
  event_type STRING,
  actor STRING,       -- tool name / retrieval source / notification sender
  outcome STRING,
  detail STRING,       -- JSON blob, shape depends on event_type
  recorded_at TIMESTAMP
);

-- Single row, id='default'. The watermark: /sweep's Branch A uses
-- last_swept_at as its `after:` cutoff instead of a fixed rolling window,
-- so a skipped or failed sweep gets caught by the next successful one
-- instead of silently losing whatever arrived during the gap. See
-- developer-docs/EMAIL-POLLER-DESIGN.md.
CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.sweep_state` (
  id STRING NOT NULL,
  last_swept_at TIMESTAMP
);

-- Intake-level thread tracking, deliberately separate from whatever the
-- agent's own tools record in `briefs` about a built deck. This is what
-- Branch A's thread-dedup and the sheet-row-dedup check against — not a
-- live read of the Sheet itself.
CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.ingestion_threads` (
  thread_id STRING NOT NULL,
  status STRING,          -- 'building' | 'built' | 'failed'
  triggered_by STRING,    -- 'branch_a' | 'branch_b'
  sheet_row_written BOOL,
  brief_id STRING,
  received_at TIMESTAMP,  -- when the email arrived; time to first draft = built updated_at - this
  created_at TIMESTAMP,
  updated_at TIMESTAMP
);
