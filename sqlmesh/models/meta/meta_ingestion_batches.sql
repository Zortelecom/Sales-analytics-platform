/*
  One row per ingestion batch.

  landing.file_registry is the first half of the meta schema: it already
  records every file the pipeline touched, with row counts and outcomes. This
  turns it into something you can trend, which a JSON report on disk cannot be.

  Watch for: a batch where files_failed > 0, or where files_skipped equals
  files_seen (nothing new arrived, which is fine on a Tuesday and alarming on
  the first of the month).
*/
MODEL (
  name meta.ingestion_batches,
  kind VIEW,
  owner analytics_team,
  description 'Per-batch ingestion outcome, from landing.file_registry'
);

SELECT
  batch_id,
  MIN(registered_at) AS started_at,
  MAX(registered_at) AS finished_at,
  DATE_DIFF('second', MIN(registered_at), MAX(registered_at)) AS duration_seconds,

  COUNT(DISTINCT source_path) AS files_seen,
  COUNT(DISTINCT source_path) FILTER (WHERE status = 'ingested') AS files_ingested,
  COUNT(DISTINCT source_path) FILTER (WHERE status = 'skipped_duplicate') AS files_skipped,
  COUNT(DISTINCT source_path) FILTER (WHERE status = 'failed') AS files_failed,

  COUNT(DISTINCT landing_table) FILTER (WHERE status = 'ingested') AS tables_written,
  COALESCE(SUM(row_count) FILTER (WHERE status = 'ingested'), 0) AS rows_landed,

  STRING_AGG(DISTINCT source_type, ', ' ORDER BY source_type) AS source_types
FROM landing.file_registry
WHERE batch_id NOT IN ('check', 'verify', 'parity-check')
GROUP BY batch_id;
