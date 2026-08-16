/*
  Denormalised sell-in lines with product and client attributes.

  Ported from serving/templates/bi_views.sql.

  WHY IT MOVED
  ────────────
  These views were raw SQL applied to serving.db AFTER each sync, so SQLMesh
  knew nothing about them. Two consequences showed up immediately on inspection:

    * v_sales_base still selected fact_sales.unit_price_actual, a column the
      pricing rework renamed. Nothing caught it -- it would have surfaced as a
      broken dashboard.
    * v_product_kpi and v_innovation_kpi were each defined TWICE in the script.
      The later definition silently won (it added subregion); the earlier one
      was dead code nobody could see.

  As SQLMesh models both are impossible: a renamed upstream column fails at
  plan time, and two models with the same name is a hard error.


  (2026-08) REBUILT AGAINST THE CURRENT fact_kp_sd.

  The ported version carried a bug that predates the port:

      CASE WHEN COALESCE(TRY_CAST(f.source_asserted_destocked AS BOOLEAN), FALSE)
                <> COALESCE(TRY_CAST(c.is_destocked AS BOOLEAN), FALSE)
           THEN TRUE ELSE FALSE END AS destocked_flag_mismatch

  source_asserted_destocked was the `kp_destocke` column, which holds the KEY
  PLAYER NAME -- "Henri & Freres" -- not a flag. TRY_CAST of a name to BOOLEAN
  is NULL, coalesced to FALSE, so the comparison reduced to
  "FALSE <> c.is_destocked": it reported a mismatch for EVERY destocked SD and
  never for any other. The whole column was noise.

  fact_kp_sd now computes destockage_channel_conflict correctly, comparing
  file provenance against the SD's master-data flag resolved at sale_date.
  This view reads that instead of recomputing it.

  Materialised as VIEW: no storage, always current with the marts underneath,
  and every consumer -- Streamlit, Superset, Metabase, the Parquet publish --
  reads the same definition instead of a per-tool copy.
*/
MODEL (
  name bi.v_kp_sd_base,
  kind VIEW,
  owner analytics_team,
  cron '@daily',
  description 'Denormalised sell-in lines with product and client attributes'
);

SELECT
    f.kp_sd_line_id,
    f.sale_date,
    f.sale_year,
    f.sale_month,
    f.sku,
    f.product_category,
    f.clientsd_id,
    f.quantity,
    f.total_amount,

    -- Three prices, not interchangeable. See fact_kp_sd.
    --   sheet     = the workbook's Ref_Products lookup (traditional-trade
    --               price). Traceability only -- never aggregate it.
    --   effective = total_amount / quantity. The price actually charged.
    --   standard  = the SD-tier reference for this SKU and date.
    f.price_tier,
    f.unit_price_sheet,
    f.unit_price_effective,
    f.unit_price_standard,
    f.price_variance_pct,

    -- KP comes from the TRANSACTION, not from the SD's master record. Drive
    -- KP rollups off kp_name_normalized; c.key_player below is the SD's KP of
    -- record and stops being meaningful the day an SD has two suppliers.
    f.kp_name,
    f.kp_name_normalized,
    f.kp_of_record_sd,
    f.kp_mismatch,

    -- Destockage: line level vs SD level.
    --   destockage_channel = which workbook the line arrived in
    --   is_destocked_sd    = whether the SD is a destocking client at all
    f.destockage_channel,
    f.is_destocked_sd,
    f.destockage_channel_conflict,

    f.sku_was_remapped,

    c.sd_name           AS client_name,
    c.region,
    c.subregion,
    c.city,
    c.supervisor_name,
    c.key_player

FROM marts.fact_kp_sd f
LEFT JOIN marts.dim_clientsd c ON f.clientsd_key = c.clientsd_key
    AND f.sale_date >= c.valid_from
    AND (f.sale_date < c.valid_to OR c.valid_to IS NULL);
