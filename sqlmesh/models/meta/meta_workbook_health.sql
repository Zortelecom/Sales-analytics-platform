/*
  One row per source workbook: how many failing rows it currently has, across
  every audit.

  The list to hand out. Ordered by failing rows, so the conversation is with
  the two supervisors who account for most of the problem rather than with
  everyone.

  Joins arrival information from meta.source_files, because the two questions
  a supervisor gets asked are usually the same pair: "is your file arriving"
  and "is it clean".
*/
MODEL (
  name meta.workbook_health,
  kind VIEW,
  owner analytics_team,
  description 'Failing rows per source workbook in the latest audit run'
);

WITH latest AS (
  SELECT * FROM meta.audit_failures WHERE run_recency = 1
),
per_file AS (
  SELECT
    source_file,
    COUNT(*)                                          AS failing_rows,
    COUNT(DISTINCT audit)                             AS distinct_audits,
    COUNT(DISTINCT sheet_name)                        AS sheets_affected,
    COUNT(*) FILTER (WHERE severity = 'error')        AS error_severity_rows,
    STRING_AGG(DISTINCT audit, ', ' ORDER BY audit)   AS audits,
    STRING_AGG(DISTINCT sheet_name, ', ' ORDER BY sheet_name) AS sheets,
    MAX(run_at)                                       AS checked_at
  FROM latest
  GROUP BY source_file
)
SELECT
  COALESCE(p.source_file, s.source_file) AS source_file,
  s.source_type,
  s.last_ingested_at,
  s.days_since_ingested,
  s.last_row_count,
  COALESCE(p.failing_rows, 0)        AS failing_rows,
  COALESCE(p.error_severity_rows, 0) AS error_severity_rows,
  COALESCE(p.distinct_audits, 0)     AS distinct_audits,
  p.sheets_affected,
  p.audits,
  p.sheets,
  p.checked_at
FROM per_file p
FULL OUTER JOIN meta.source_files s
  ON p.source_file = s.source_file;
