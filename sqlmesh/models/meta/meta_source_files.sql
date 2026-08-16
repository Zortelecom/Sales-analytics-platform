/*
  Current inventory of source workbooks, one row per file.

  The arrival-lag column is the useful one. A supervisor who stops sending a
  workbook produces no error anywhere -- the pipeline simply has nothing new
  from them, and the sell-out figures for that subregion quietly flatten.
  This is the only place that becomes visible.

  ingest_count > 1 means the workbook was resubmitted: the earlier rows are
  still in landing, superseded by raw.current_files. That is the audit trail
  for a correction.
*/
MODEL (
  name meta.source_files,
  kind VIEW,
  owner analytics_team,
  description 'Latest ingest of each source workbook, with arrival lag'
);

WITH ranked AS (
  SELECT
    r.*,
    ROW_NUMBER() OVER (
      PARTITION BY r.source_path, r.landing_table
      ORDER BY r.registered_at DESC
    ) AS recency,
    -- FILTER goes BEFORE OVER on a window function, not after.
    COUNT(*) FILTER (WHERE r.status = 'ingested') OVER (
      PARTITION BY r.source_path, r.landing_table
    ) AS ingest_count
  FROM landing.file_registry r
  WHERE r.status IN ('ingested', 'failed')
)
SELECT
  source_type,
  source_file,
  source_path,
  landing_table,
  file_sha256,
  file_size_bytes,
  file_mtime,
  batch_id        AS last_batch_id,
  registered_at   AS last_ingested_at,
  row_count       AS last_row_count,
  status          AS last_status,
  error           AS last_error,
  ingest_count,
  DATE_DIFF('day', file_mtime, CURRENT_TIMESTAMP)     AS days_since_modified,
  DATE_DIFF('day', registered_at, CURRENT_TIMESTAMP)  AS days_since_ingested
FROM ranked
WHERE recency = 1;