MODEL (
  name marts.fact_promo_gains_actual,
  kind FULL,
  start '2026-04-01',
  audits (
    assert_gain_values_non_negative
  )
);

-- Interim / short-term fact: transcribes gains already computed by hand in
-- the bouclage closing files (promo_bouclage_extractor.py). No tier
-- resolution happens here — this table IS the ground truth for April 2026,
-- produced by finance/ops, not derived from raw transactions.
--
-- Once fact_promo_sku_tier_attainment / fact_promo_ca_attainment (the
-- generic engine) are live, this table becomes the reconciliation
-- reference: compare computed vs actual here to validate the engine,
-- rather than being thrown away.

SELECT
    source_file,
    sheet_name,
    channel_type,                 -- 'DG' | 'SD'
    period_label,                 -- 'YYYY-MM'
    region,
    kp_name_resolved AS kp_name,
    vendeur,
    client_key,                   -- SD name (channel=SD) or end-retail-client name (channel=DG)
    product_label,
    unit,
    CAST(value AS DECIMAL(18,2)) AS gain_qty,
    ingestion_batch_id,
    ingestion_ts
FROM staging.stg_promo_gains_actual        -- output of PromoBouclageExtractor.melt_to_long(), metric_type = 'GAIN'
WHERE value IS NOT NULL
