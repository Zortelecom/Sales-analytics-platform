/*
  Failing rows, grouped by the workbook and sheet they came from.

  This is the point of the whole data-quality asset. A SQLMesh audit reports
  "12 rows failed"; converting that into "Banok Serge's tab in
  ExSD-Sales-Est.xlsx has 12 unmatched SKUs" required a hand-written query,
  which is not something a supervisor will run.

  Reachable only because landing keeps _source_file / _sheet_name / _row_num
  and the facts now carry them through. Before that, the closest available
  identifier was filename_subregion -- a slug derived from the filename rather
  than the file.

  Capped at 500 failing rows per audit per run upstream: a rule failing on
  30,000 rows is a broken rule, not 30,000 problems.
*/
MODEL (
  name meta.audit_failures,
  kind VIEW,
  owner analytics_team,
  description 'Failing rows with source workbook, sheet and row position'
);

SELECT
  f.run_at,
  f.run_id,
  f.audit,
  f.entity,
  r.question,
  r.severity,
  f.source_file,
  f.sheet_name,
  f.source_row_num,
  f.detail,
  -- Ranked so the newest run can be filtered without knowing its id.
  DENSE_RANK() OVER (ORDER BY f.run_at DESC) AS run_recency
FROM landing.audit_failures f
LEFT JOIN landing.audit_results r
  ON f.run_id = r.run_id AND f.audit = r.audit;
