/*
  KP-native SKU -> internal SKU.

  (2026-08) The QUALIFY had no ORDER BY:

      QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(UPPER(kp_sku))) = 1

  With duplicate kp_sku values -- which ReferenceExtractor already warns
  about -- which row survived was non-deterministic and could differ between
  runs on identical input. A mapping that changes silently between runs is
  worse than one that is wrong consistently, because the wrong one is at least
  findable.

  Deduplication is now explicit: the row with the latest _ingested_at wins,
  then the last-listed row in the sheet. Both are stable given stable input.

  Note this mapping is a SAFETY NET, not the main path. Supervisors resolve KP
  SKUs to internal SKUs inside the workbook before pasting, so stg_kp_sd_data's
  join misses on essentially every row -- see sku_was_remapped there, where
  TRUE is the anomaly.
*/
MODEL (
  name staging.stg_kp_sku_mapping,
  kind FULL,
  cron '@daily',
  owner analytics_team,
  grain (kp_sku),
  audits (
    unique_values(columns := (kp_sku)),
    not_null(columns := (kp_sku, internal_sku))
  ),
  columns (
    kp_sku TEXT,
    internal_sku TEXT,
    duplicate_count INTEGER
  )
);

SELECT
  TRIM(UPPER(kp_sku))       AS kp_sku,
  TRIM(UPPER(internal_sku)) AS internal_sku,
  -- Surfaced rather than hidden: >1 means the sheet holds conflicting mappings
  -- for one KP SKU, and someone should decide which is right.
  CAST(COUNT(*) OVER (PARTITION BY TRIM(UPPER(kp_sku))) AS INTEGER) AS duplicate_count
FROM raw.kp_sku_mapping_data
WHERE kp_sku IS NOT NULL
  AND internal_sku IS NOT NULL
  AND TRIM(kp_sku) != ''
  AND TRIM(internal_sku) != ''
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY TRIM(UPPER(kp_sku))
  ORDER BY _ingested_at DESC, _row_num DESC
) = 1;