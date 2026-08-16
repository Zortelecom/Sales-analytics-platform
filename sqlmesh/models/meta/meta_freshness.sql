/*
  Business-date freshness per source, from the landing data itself.

  Distinct from meta.source_files, which measures when a FILE arrived. A
  supervisor can send a workbook on time that contains nothing newer than last
  month -- the file is fresh, the data is not. Both numbers are here so the
  difference is visible.

  Deliberately not an audit. A flat "stale after 7 days" rule fires on Sundays,
  public holidays, a supervisor on leave, and any KP that invoices twice a
  month; and it fails every backfill, since it compares against wall clock. A
  trend per source, read by a human, is worth more than a boolean that cries
  wolf. See assert_sales_data_is_fresh for the audit-shaped version and its
  caveats.
*/
MODEL (
  name meta.freshness,
  kind VIEW,
  owner analytics_team,
  description 'Latest business date and file arrival per source'
);

WITH business_dates AS (
  SELECT 'sales_data' AS landing_table, MAX(CAST(sale_date AS DATE)) AS max_business_date
  FROM raw.sales_data
  UNION ALL
  SELECT 'kp_sd_destocke_data', MAX(CAST(sale_date AS DATE)) FROM raw.kp_sd_destocke_data
  UNION ALL
  SELECT 'kp_sd_non_destocke_data', MAX(CAST(sale_date AS DATE)) FROM raw.kp_sd_non_destocke_data
)
SELECT
  b.landing_table,
  b.max_business_date,
  i.last_ingested_at,
  i.current_rows,
  DATE_DIFF('day', b.max_business_date, CURRENT_DATE) AS business_lag_days,
  DATE_DIFF('day', i.last_ingested_at, CURRENT_TIMESTAMP) AS arrival_lag_days
FROM business_dates b
LEFT JOIN meta.landing_inventory i ON b.landing_table = i.landing_table;
