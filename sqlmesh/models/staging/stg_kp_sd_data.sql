MODEL (
  name staging.stg_kp_sd_data,
  kind FULL,
  owner analytics_team,
  cron '@monthly',
  grain (kp_sd_line_id),
  columns (
    clientsd_id TEXT,
    sale_date DATE,
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
    source_asserted_destocked TEXT,
    kp_sd_line_id TEXT,
    has_sku_mapping BOOLEAN
  ),
);

WITH raw_kp_sd AS (
  SELECT
    sd_id AS clientsd_id,
    sale_date,
    sku AS source_sku,
    qty, 
    unit_price, 
    amount, 
    weight, 
    subregion, 
    channel, 
    sales_supervisor,
    TRUE AS is_destocked,
    kp_destocke AS source_asserted_destocked,
    kp_sd_line_id
  FROM raw.kp_sd_destocke_data
  WHERE sale_date IS NOT NULL AND sku IS NOT NULL

  UNION ALL

  SELECT
    sd_id AS clientsd_id,
    sale_date,
    sku AS source_sku,
    qty, 
    unit_price, 
    amount, 
    weight,
    subregion, 
    channel,
    sales_supervisor,
    FALSE AS is_destocked,
    kp_destocke AS source_asserted_destocked,
    kp_sd_line_id
  FROM raw.kp_sd_non_destocke_data
  WHERE sale_date IS NOT NULL AND sku IS NOT NULL
)
SELECT
  r.clientsd_id,
  CAST(r.sale_date AS DATE) AS sale_date,
  TRIM(UPPER(r.source_sku)) AS source_sku,
  COALESCE(m.internal_sku, TRIM(UPPER(r.source_sku))) AS sku,
  CAST(r.qty AS INTEGER) AS quantity,
  CAST(r.unit_price AS DECIMAL(18,2)) AS unit_price,
  CAST(r.amount AS DECIMAL(18,2)) AS total_amount,
  CAST(r.weight AS DECIMAL(18,4)) AS unit_weight_kg,
  r.subregion,
  r.channel,
  r.sales_supervisor,
  r.is_destocked,
  r.source_asserted_destocked,
  r.kp_sd_line_id,
  m.internal_sku IS NOT NULL AS has_sku_mapping
FROM raw_kp_sd r
LEFT JOIN staging.stg_kp_sku_mapping m
  ON TRIM(UPPER(r.source_sku)) = m.kp_sku;