MODEL (
  name marts.fact_kp_sd,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
  ),
  start '2025-01-01',
  cron '@monthly',
  grain (kp_sd_line_id),
  owner analytics_team,
  storage_format 'parquet',
  partitioned_by (sale_year, sale_month),
  audits (
    unique_values(columns := (kp_sd_line_id)),
    not_null(columns := (kp_sd_line_id, sale_date, sku, clientsd_id)),
    accepted_range(column := total_amount, min_v := 0, inclusive := false),
    accepted_range(column := quantity,     min_v := 0, inclusive := false),
    assert_no_orphaned_product_kp,
    assert_amount_is_integer_xaf_kp,
    assert_amount_matches_qty_x_price_kp

    -- , assert_no_orphaned_client_kp
    -- , assert_destocked_flag_consistency
  )
);

SELECT
  s.kp_sd_line_id,
  CAST(STRFTIME(s.sale_date, '%Y%m%d') AS INTEGER) AS date_key,
  s.sale_date,
  EXTRACT(YEAR  FROM s.sale_date) AS sale_year,
  EXTRACT(MONTH FROM s.sale_date) AS sale_month,

  p.product_key,
  s.sku,
  p.product_category,

  c.clientsd_key,
  s.clientsd_id,
  c.is_destocked        AS dim_is_destocked,
  s.source_asserted_destocked,

  s.quantity,
  s.unit_price,
  s.total_amount

FROM staging.stg_kp_sd_data s

LEFT JOIN marts.dim_products p
  ON s.sku = p.sku
  AND s.sale_date >= p.valid_from
  AND (s.sale_date < p.valid_to OR p.valid_to IS NULL)

LEFT JOIN marts.dim_clientsd c
  ON s.clientsd_id = c.sd_id
  AND s.sale_date >= c.valid_from
  AND (s.sale_date < c.valid_to OR c.valid_to IS NULL);
