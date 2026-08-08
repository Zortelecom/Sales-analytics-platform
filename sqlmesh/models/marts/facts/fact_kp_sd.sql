/*
  Sell-in fact: KP -> SD.

  (2026-08) Four changes:

  1. Added the interval filter. This model is INCREMENTAL_BY_TIME_RANGE but the
     query had no WHERE clause on sale_date, so every interval rebuilt full
     history.

  2. KP is now carried from the transaction, not inherited from the SD.
     `kp_destocke` in the source file is the Key Player NAME, and it was
     entering this table as `source_asserted_destocked` -- a boolean-sounding
     alias for a dimension key. It is now kp_name / kp_name_normalized.

     dim_clientsd.key_player is retained as kp_of_record_sd. Today one KP serves each
     SD, so the two agree and either could drive v_kp_performance_kpi. That
     stops being true the moment a second KP ships to the same SD: the
     dimension attribute would attribute that KP's revenue to the SD's KP of
     record. Build KP measures on kp_name_normalized, not on the dimension.

     kp_mismatch is the early-warning signal for that transition. Today a TRUE
     is a data error. When it starts firing systematically for one SD, that SD
     has a second supplier and kp_of_record_sd is obsolete for it.

  3. Destockage provenance carried through. s.is_destocked (which workbook the
     line arrived in) was never selected here, which is why
     assert_destocked_flag_consistency had to stay commented out -- the column
     it needed did not exist in this model.

  4. has_sku_mapping -> sku_was_remapped (see stg_kp_sd_data).

  Two columns describe destockage and they are NOT interchangeable:
    destockage_channel : line level. How THIS shipment was treated. Use this
                         for sell-in measures and promo attainment.
    is_destocked_sd    : SD level, from dim_clientsd. Whether this SD is a
                         destocking client at all.
*/
MODEL (
  name marts.fact_kp_sd,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column sale_date
  ),
  start '2024-10-01',
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
    assert_no_orphaned_client_kp,
    assert_amount_is_integer_xaf_kp,
    assert_amount_matches_qty_x_price_kp,
    assert_no_unmapped_kp_sku,
    assert_destockage_channel_matches_sd,
    assert_kp_matches_sd_master
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
  s.source_sku,
  p.product_category,

  c.clientsd_key,
  s.clientsd_id,

  -- KP from the transaction. Drive KP measures off kp_name_normalized.
  s.kp_name,
  s.kp_name_normalized,
  -- KP from SD master data. Kept for reconciliation, not for aggregation.
  c.key_player AS kp_of_record_sd,
  s.kp_name_normalized IS NOT NULL
    AND c.key_player IS NOT NULL
    AND s.kp_name_normalized IS DISTINCT FROM
        UPPER(REGEXP_REPLACE(TRIM(c.key_player), '\s+', ' ', 'g'))
    AS kp_mismatch,

  -- Destockage: line level vs SD level.
  s.is_destocked AS destockage_channel,
  c.is_destocked AS is_destocked_sd,
  s.is_destocked IS NOT NULL
    AND c.is_destocked IS NOT NULL
    AND s.is_destocked IS DISTINCT FROM c.is_destocked
    AS destockage_channel_conflict,

  s.quantity,
  s.unit_price,
  s.total_amount,
  s.sku_was_remapped

FROM staging.stg_kp_sd_data s

LEFT JOIN marts.dim_products p
  ON s.sku = p.sku
  AND s.sale_date >= p.valid_from
  AND (s.sale_date < p.valid_to OR p.valid_to IS NULL)

LEFT JOIN marts.dim_clientsd c
  ON s.clientsd_id = c.sd_id
  AND s.sale_date >= c.valid_from
  AND (s.sale_date < c.valid_to OR c.valid_to IS NULL)

WHERE s.sale_date BETWEEN @start_ds AND @end_ds;