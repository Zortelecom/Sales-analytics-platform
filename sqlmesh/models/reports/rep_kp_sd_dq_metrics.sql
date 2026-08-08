/*
  Sell-in data quality, grouped by SD, KP and month.

  The grouping is the point. A flat count says "there are 340 KP mismatches".
  Grouped by SD it says "94% come from two SDs" -- a conversation with two
  supervisors rather than a data quality initiative. Amounts are included so
  the list can be ranked by value at stake rather than by row volume.

  Grouping by kp_name_normalized also makes the multi-KP transition visible
  before it breaks anything: the day a single clientsd_id shows two KP rows in
  the same month, dim_clientsd.kp has stopped describing that SD.

  When the meta schema lands this becomes an append to meta.dq_metrics with a
  run_id, so the rates can be trended instead of only snapshotted.
*/
MODEL (
  name reports.rep_kp_sd_dq_metrics,
  kind FULL,
  start '2024-10-01',
  cron '@monthly',
  owner analytics_team,
  grain (sale_year, sale_month, clientsd_id, kp_name_normalized)
);

SELECT
  f.sale_year,
  f.sale_month,
  f.clientsd_id,
  f.kp_name_normalized,

  COUNT(*) AS total_rows,
  SUM(f.total_amount) AS total_amount,

  COUNT(*) FILTER (WHERE f.kp_mismatch)                 AS kp_mismatch_rows,
  COUNT(*) FILTER (WHERE f.destockage_channel_conflict)  AS destockage_conflict_rows,
  COUNT(*) FILTER (WHERE f.sku_was_remapped)             AS unresolved_sku_rows,
  COUNT(*) FILTER (WHERE f.product_key IS NULL)          AS orphan_product_rows,
  COUNT(*) FILTER (WHERE f.clientsd_key IS NULL)         AS orphan_client_rows,
  COUNT(*) FILTER (WHERE f.kp_name IS NULL)              AS missing_kp_rows,

  CAST(COUNT(*) FILTER (WHERE f.kp_mismatch) AS DOUBLE)
    / NULLIF(COUNT(*), 0) AS kp_mismatch_rate,
  CAST(COUNT(*) FILTER (WHERE f.destockage_channel_conflict) AS DOUBLE)
    / NULLIF(COUNT(*), 0) AS destockage_conflict_rate,
  CAST(COUNT(*) FILTER (WHERE f.sku_was_remapped) AS DOUBLE)
    / NULLIF(COUNT(*), 0) AS unresolved_sku_rate,

  -- Value at stake, for ranking by impact.
  SUM(f.total_amount) FILTER (WHERE f.kp_mismatch)
    AS kp_mismatch_amount,
  SUM(f.total_amount) FILTER (WHERE f.destockage_channel_conflict)
    AS destockage_conflict_amount

FROM marts.fact_kp_sd f
GROUP BY 1, 2, 3, 4;
