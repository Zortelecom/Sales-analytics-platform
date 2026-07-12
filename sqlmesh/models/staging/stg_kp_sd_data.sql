MODEL (
  name staging.stg_kp_sd_data,
  kind FULL,
  owner analytics_team,
  cron '@monthly',
  grain (kp_sd_line_id),
  columns (
    clientsd_id TEXT,
    sale_date DATE,
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
    kp_sd_line_id TEXT
  ),
);

SELECT
  sd_id AS clientsd_id,
  CAST(sale_date AS DATE) AS sale_date,
  sku,
  CAST(qty AS INTEGER) AS quantity,
  CAST(unit_price AS DECIMAL(18,2)) AS unit_price,
  CAST(amount AS DECIMAL(18,2)) AS total_amount,
  CAST(weight AS DECIMAL(18,4)) AS unit_weight_kg,
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
  CAST(sale_date AS DATE) AS sale_date,
  sku,
  CAST(qty AS INTEGER) AS quantity,
  CAST(unit_price AS DECIMAL(18,2)) AS unit_price,
  CAST(amount AS DECIMAL(18,2)) AS total_amount,
  CAST(weight AS DECIMAL(18,4)) AS unit_weight_kg,
  subregion,
  channel,
  NULL AS sales_supervisor,
  FALSE AS is_destocked,
  NULL AS source_asserted_destocked,
  kp_sd_line_id
FROM raw.kp_sd_non_destocke_data
WHERE sale_date IS NOT NULL AND sku IS NOT NULL;