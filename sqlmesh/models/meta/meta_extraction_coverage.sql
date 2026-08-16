/*
  Which sheet and table each landing row came from, and how many rows each
  produced.

  This exists because of two silent near-misses: SalesIn_Mbemezok yielded 1 row
  while its neighbours yielded hundreds, and SalesNgono in ExSD-Sales-GMS
  yielded 1 row while the same salesperson yielded 1,060 in Yde_Nord. Neither
  raised anything -- a table with one row is structurally valid.

  A sheet whose row count collapses between runs is the signal that a
  supervisor cleared a tab, renamed a Table, or pasted over a range. Only
  possible because landing keeps _sheet_name and _table_name, which SeedWriter
  used to discard.
*/
MODEL (
  name meta.extraction_coverage,
  kind VIEW,
  owner analytics_team,
  description 'Rows per source sheet and Excel Table, for spotting collapsed tabs'
);

WITH per_sheet AS (
  SELECT 'sales_data' AS landing_table, _source_file, _sheet_name, _table_name,
         _batch_id, COUNT(*) AS rows_extracted
  FROM raw.sales_data GROUP BY 1, 2, 3, 4, 5
  UNION ALL
  SELECT 'kp_sd_destocke_data', _source_file, _sheet_name, _table_name,
         _batch_id, COUNT(*)
  FROM raw.kp_sd_destocke_data GROUP BY 1, 2, 3, 4, 5
  UNION ALL
  SELECT 'kp_sd_non_destocke_data', _source_file, _sheet_name, _table_name,
         _batch_id, COUNT(*)
  FROM raw.kp_sd_non_destocke_data GROUP BY 1, 2, 3, 4, 5
)
SELECT
  landing_table,
  _source_file AS source_file,
  _sheet_name  AS sheet_name,
  _table_name  AS table_name,
  _batch_id    AS batch_id,
  rows_extracted,
  ROUND(
    100.0 * rows_extracted
    / NULLIF(SUM(rows_extracted) OVER (PARTITION BY landing_table, _source_file), 0),
    2
  ) AS pct_of_file
FROM per_sheet;
