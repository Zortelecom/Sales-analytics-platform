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

     Note both destockage columns are correctly time-aware: is_destocked is
     an SCD2 check column on dim_clientsd, so the join below resolves the
     SD's status AS AT sale_date, not its status today. That is what let
     assert_destockage_channel_matches_sd surface historical rows filed
     into the wrong workbook after an SD switched to destocké.

  3. Destockage provenance carried through. s.is_destocked (which workbook the
     line arrived in) was never selected here, which is why
     assert_destocked_flag_consistency had to stay commented out -- the column
     it needed did not exist in this model.

  4. has_sku_mapping -> sku_was_remapped (see stg_kp_sd_data).

  5. Prices split three ways, as in fact_sales. unit_price in the KP workbook
     is a lookup of the Ref_Products traditional-trade price; the actual sell-in
     price is total_amount / quantity, and the reference is the SD tier in
     dim_product_price. Sell-in is a step further up the chain than traditional
     trade, so it is lower by construction -- which is why the old amount audit
     fired on every sell-in line.

  Two columns describe destockage and they are NOT interchangeable:
    destockage_channel : line level. How THIS shipment was treated. Use this
                         for sell-in measures and promo attainment.
    is_destocked_sd    : SD level, from dim_clientsd. Whether this SD is a
                         destocking client at all.

  PARTITIONING
  ────────────
  No partitioned_by. SQLMesh ALWAYS partitions an INCREMENTAL_BY_TIME_RANGE
  model by its time column, and an explicit partitioned_by is APPENDED to that
  rather than replacing it. Declaring (sale_year, sale_month) therefore
  produced sale_date=.../sale_year=.../sale_month=... -- three levels, ~1770
  directories instead of ~590, and 27 more characters of path on a filesystem
  with 7 characters of headroom left. sale_date is already finer-grained than
  month, so the extra levels partition nothing.
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
  audits (
    unique_values(columns := (kp_sd_line_id)),
    not_null(columns := (kp_sd_line_id, sale_date, sku, clientsd_id)),
    -- accepted_range on quantity/total_amount is redundant now:
    -- assert_line_signs_are_coherent_kp covers <= 0 on both AND the sign
    -- mismatch a range check cannot see.
    assert_no_orphaned_product_kp,
    assert_no_orphaned_client_kp,
    -- assert_amount_is_integer_xaf_kp removed: see fact_sales for why.
    assert_line_signs_are_coherent_kp,
    -- An SD returning stock to a KP is a real transaction, so negatives are
    -- reported rather than blocked. Sign coherence stays blocking above.
    assert_no_negative_sellin,
    -- assert_amount_matches_qty_x_price_kp is DELIBERATELY NOT LISTED.
    -- It compared total_amount against quantity x unit_price, where
    -- unit_price is the workbook's Ref_Products lookup -- the TRADITIONAL
    -- TRADE price. Sell-in is a step further up the chain, so that equality
    -- never held and the audit fired on every line. (It also still selects
    -- `unit_price`, which is now unit_price_sheet here, so it fails to bind.)
    --
    -- There is no arithmetic check to make on this table: unit_price_effective
    -- is DEFINED as total_amount / quantity, so the identity is trivially
    -- true. The real question -- is the price close to the SD reference --
    -- is assert_price_matches_tier_kp below.
    assert_price_matches_tier_kp,
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

  -- PRICES. Sell-in is one step up the chain, so the SD tier is lower than
  -- TT by construction -- comparing it against Ref_Products' TT price was why
  -- assert_amount_matches_qty_x_price_kp fired on every line.
  'SD' AS price_tier,
  s.quantity,
  s.unit_price                                     AS unit_price_sheet,
  ROUND(s.total_amount / NULLIF(s.quantity, 0), 2) AS unit_price_effective,
  pp.unit_price                                    AS unit_price_standard,
  s.total_amount,
  CASE
    WHEN pp.unit_price > 0 AND s.quantity > 0
    THEN ROUND(
      ((s.total_amount / s.quantity) - pp.unit_price) / pp.unit_price * 100, 2)
  END AS price_variance_pct,
  s.sku_was_remapped,

  -- Provenance for the data-quality report. For destocké workbooks the sheet
  -- name is the SD, so this is often more reliable than the sd_name column.
  s.source_file,
  s.sheet_name,
  s.source_row_num

FROM staging.stg_kp_sd_data s

LEFT JOIN marts.dim_products p
  ON s.sku = p.sku
  AND s.sale_date >= p.valid_from
  AND (s.sale_date < p.valid_to OR p.valid_to IS NULL)

LEFT JOIN marts.dim_product_price pp
  ON s.sku = pp.sku
  AND pp.price_tier = 'SD'
  AND s.sale_date >= pp.valid_from
  AND (s.sale_date < pp.valid_to OR pp.valid_to IS NULL)

LEFT JOIN marts.dim_clientsd c
  ON s.clientsd_id = c.sd_id
  AND s.sale_date >= c.valid_from
  AND (s.sale_date < c.valid_to OR c.valid_to IS NULL)

WHERE s.sale_date BETWEEN @start_ds AND @end_ds;