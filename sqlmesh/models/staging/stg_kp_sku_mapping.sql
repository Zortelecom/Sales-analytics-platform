MODEL (
  name staging.stg_kp_sku_mapping,
  kind FULL,
  owner analytics_team,
  grain (kp_sku),
  columns (
    kp_sku TEXT,
    internal_sku TEXT
  )
);

SELECT
  TRIM(UPPER(kp_sku)) AS kp_sku,
  TRIM(UPPER(internal_sku)) AS internal_sku
FROM raw.kp_sku_mapping_data
WHERE kp_sku IS NOT NULL
  AND internal_sku IS NOT NULL
QUALIFY ROW_NUMBER() OVER (PARTITION BY TRIM(UPPER(kp_sku))) = 1;