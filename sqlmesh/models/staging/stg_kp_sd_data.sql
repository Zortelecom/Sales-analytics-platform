/*
  Sell-in staging: destocké + non-destocké unioned.

  (2026-08) Two changes:

  1. source_asserted_destocked -> kp_name.
     The `kp_destocke` source column holds the KEY PLAYER NAME ("Henri &
     Freres"), not a destockage assertion. Under the old alias it entered
     fact_kp_sd as a boolean-sounding TEXT column, where any measure author
     would reasonably read it as a flag. It is a dimension key.

     kp_name_normalized collapses case and repeated whitespace so
     "henri  &  freres" and "Henri & Freres" resolve to one KP. It does NOT
     fix "&" vs "et", accents, or abbreviations -- those need a Ref_KP table
     with aliases. Add one before a second KP is onboarded; retrofitting KP
     identity across a year of history is a much larger job.

  2. has_sku_mapping -> sku_was_remapped, with inverted meaning.
     Supervisors resolve KP-native SKUs to internal SKUs inside the workbook
     before pasting into the extraction table (the mapping sheet also powers
     their pivot tables). So source_sku already holds internal SKUs and the
     join to stg_kp_sku_mapping misses on essentially every row -- which is the
     HEALTHY state, not a gap. The join is kept as a safety net: TRUE here
     means someone pasted an unresolved KP SKU and the pipeline caught it.

  is_destocked stays what it always was: file provenance, i.e. which workbook
  the row arrived in. It is compared against dim_clientsd.is_destocked one
  layer up, in fact_kp_sd.
*/
MODEL (
  name staging.stg_kp_sd_data,
  kind FULL,
  owner analytics_team,
  cron '@monthly',
  grain (kp_sd_line_id),
  columns (
    clientsd_id TEXT,
    sale_date DATE,
    kp_name TEXT,
    kp_name_normalized TEXT,
    source_sku TEXT,
    sku TEXT,
    quantity INT,
    unit_price DECIMAL(18,2),
    total_amount DECIMAL(18,2),
    unit_weight_kg DECIMAL(18,4),
    subregion TEXT,
    channel TEXT,
    sales_supervisor TEXT,
    is_destocked BOOLEAN,
    kp_sd_line_id TEXT,
    sku_was_remapped BOOLEAN
  ),
);

WITH raw_kp_sd AS (
  SELECT
    sd_id AS clientsd_id,
    sale_date,
    kp_destocke AS kp_name,
    sku AS source_sku,
    qty,
    unit_price,
    amount,
    weight,
    subregion,
    channel,
    sales_supervisor,
    TRUE AS is_destocked,
    kp_sd_line_id
  FROM raw.kp_sd_destocke_data
  WHERE sale_date IS NOT NULL AND sku IS NOT NULL

  UNION ALL

  SELECT
    sd_id AS clientsd_id,
    sale_date,
    kp_destocke AS kp_name,
    sku AS source_sku,
    qty,
    unit_price,
    amount,
    weight,
    subregion,
    channel,
    sales_supervisor,
    FALSE AS is_destocked,
    kp_sd_line_id
  FROM raw.kp_sd_non_destocke_data
  WHERE sale_date IS NOT NULL AND sku IS NOT NULL
)

SELECT
  r.clientsd_id,
  CAST(r.sale_date AS DATE) AS sale_date,

  NULLIF(TRIM(r.kp_name), '') AS kp_name,
  -- Join key. Collapses case + repeated whitespace only.
  UPPER(REGEXP_REPLACE(TRIM(NULLIF(TRIM(r.kp_name), '')), '\s+', ' ', 'g'))
    AS kp_name_normalized,

  TRIM(UPPER(r.source_sku)) AS source_sku,
  COALESCE(m.internal_sku, TRIM(UPPER(r.source_sku))) AS sku,
  CAST(r.qty AS INTEGER) AS quantity,
  CAST(r.unit_price AS DECIMAL(18,2)) AS unit_price,
  CAST(r.amount AS DECIMAL(18,2)) AS total_amount,
  CAST(r.weight AS DECIMAL(18,4)) AS unit_weight_kg,
  r.subregion,
  r.channel,
  r.sales_supervisor,

  -- File provenance: which workbook this line arrived in.
  r.is_destocked,

  r.kp_sd_line_id,

  -- TRUE = supervisor pasted an unresolved KP-native SKU; the pipeline
  -- remapped it. This is the anomaly, not the healthy state.
  m.internal_sku IS NOT NULL AS sku_was_remapped

FROM raw_kp_sd r
LEFT JOIN staging.stg_kp_sku_mapping m
  ON TRIM(UPPER(r.source_sku)) = m.kp_sku;