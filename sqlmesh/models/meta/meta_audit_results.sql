/*
  Data-quality audit outcomes, one row per audit per run.

  Written by orchestration/assets/data_quality.py into landing.audit_results;
  this exposes it, exactly as meta.ingestion_batches exposes
  landing.file_registry. The write goes to `landing` rather than `meta`
  because SQLMesh owns the meta schema and a plan would drop tables placed
  there.

  Distinct from the SQLMesh audits under sqlmesh/audits/: those stop the build,
  these attribute the problem. A rule may exist in both -- the SQLMesh one is
  authoritative for whether the pipeline proceeds.

  is_regression is the column worth watching. A count that is merely high has
  usually been high for months; a count that went UP is something that changed
  this week, and that is the one worth a phone call.
*/
MODEL (
  name meta.audit_results,
  kind VIEW,
  owner analytics_team,
  description 'Per-run data quality audit outcomes, with run-over-run change'
);

WITH ranked AS (
  SELECT
    *,
    LAG(rows_failing) OVER (
      PARTITION BY environment, audit ORDER BY run_at
    ) AS previous_rows_failing,
    ROW_NUMBER() OVER (
      PARTITION BY environment, audit ORDER BY run_at DESC
    ) AS recency
  FROM landing.audit_results
)
SELECT
  run_at,
  run_id,
  environment,
  audit,
  entity,
  question,
  severity,
  status,
  rows_checked,
  rows_failing,
  files_failing,
  previous_rows_failing,
  rows_failing - COALESCE(previous_rows_failing, rows_failing) AS change_vs_previous,
  COALESCE(previous_rows_failing, 0) < rows_failing AS is_regression,
  ROUND(100.0 * rows_failing / NULLIF(rows_checked, 0), 3) AS failure_rate_pct,
  recency = 1 AS is_latest,
  error
FROM ranked;
