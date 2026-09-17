-- Hollow schema: the real project's five tables, empty of real rows.
-- Run once via bq query, with --parameter substitution or a sed pass for
-- PROJECT/DATASET — see README.md for the exact command.

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.briefs` (
  brief_id STRING NOT NULL,
  message_id STRING,
  client_name STRING,
  status STRING,
  attempts INT64,
  detail STRING,
  deck_file_id STRING,
  deck_link STRING,
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

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.handoffs` (
  brief_id STRING NOT NULL,
  recipient STRING,
  subject STRING,
  sent_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.retrieval_log` (
  brief_id STRING NOT NULL,
  source STRING,
  status STRING,
  detail STRING,
  result_count INT64,
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

CREATE TABLE IF NOT EXISTS `PROJECT.DATASET.audit_log` (
  brief_id STRING,
  tool STRING,
  principal STRING,
  outcome STRING,
  duration_ms INT64,
  recorded_at TIMESTAMP
);
