/*
  Row counts per landing table: current, total, superseded.

  superseded_rows > 0 means a workbook was resubmitted and the earlier version
  is still queryable. That is the number that makes a correction quantifiable
  -- "N lines changed between version 1 and version 2 of this file" -- which
  the seed architecture could not answer at all, because the CSV was
  overwritten.

  Explicit UNION ALL rather than a loop: the eight tables are a fixed contract,
  and a new source should require a deliberate edit here so it cannot be
  forgotten in the observability layer.
*/
MODEL (
  name meta.landing_inventory,
  kind VIEW,
  owner analytics_team,
  description 'Current vs total vs superseded rows per landing table'
);

WITH counts AS (
  SELECT 'sales_data' AS landing_table,
         (SELECT COUNT(*) FROM landing.sales_data) AS total_rows,
         (SELECT COUNT(*) FROM raw.sales_data) AS current_rows
  UNION ALL SELECT 'targets_data',
         (SELECT COUNT(*) FROM landing.targets_data),
         (SELECT COUNT(*) FROM raw.targets_data)
  UNION ALL SELECT 'clientsd_data',
         (SELECT COUNT(*) FROM landing.clientsd_data),
         (SELECT COUNT(*) FROM raw.clientsd_data)
  UNION ALL SELECT 'products_data',
         (SELECT COUNT(*) FROM landing.products_data),
         (SELECT COUNT(*) FROM raw.products_data)
  UNION ALL SELECT 'salesteam_data',
         (SELECT COUNT(*) FROM landing.salesteam_data),
         (SELECT COUNT(*) FROM raw.salesteam_data)
  UNION ALL SELECT 'kp_sku_mapping_data',
         (SELECT COUNT(*) FROM landing.kp_sku_mapping_data),
         (SELECT COUNT(*) FROM raw.kp_sku_mapping_data)
  UNION ALL SELECT 'kp_sd_destocke_data',
         (SELECT COUNT(*) FROM landing.kp_sd_destocke_data),
         (SELECT COUNT(*) FROM raw.kp_sd_destocke_data)
  UNION ALL SELECT 'kp_sd_non_destocke_data',
         (SELECT COUNT(*) FROM landing.kp_sd_non_destocke_data),
         (SELECT COUNT(*) FROM raw.kp_sd_non_destocke_data)
),
files AS (
  SELECT
    landing_table,
    COUNT(DISTINCT source_path) FILTER (WHERE status = 'ingested') AS source_files,
    MAX(registered_at) FILTER (WHERE status = 'ingested') AS last_ingested_at
  FROM landing.file_registry
  GROUP BY landing_table
)
SELECT
  c.landing_table,
  c.current_rows,
  c.total_rows,
  c.total_rows - c.current_rows AS superseded_rows,
  f.source_files,
  f.last_ingested_at
FROM counts c
LEFT JOIN files f ON c.landing_table = f.landing_table;
