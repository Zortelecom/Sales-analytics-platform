/*
  A line whose workbook provenance contradicts the SD's master-data flag:
  a non-destocking SD appearing in a destocké workbook, or the reverse.

  This is what the commented-out assert_destocked_flag_consistency was
  reaching for. It could not be written before because fact_kp_sd never
  selected the line-level provenance flag.

  Either the SD's is_destocked attribute in Ref_ClientsSD is stale, or a
  supervisor filed rows into the wrong workbook. Both are worth a name and a
  count, neither is worth blocking the build over -- so blocking false, with
  the rate per SD tracked in reports.rep_kp_sd_dq_metrics.
*/
AUDIT (
  name assert_destockage_channel_matches_sd,
  dialect duckdb,
  blocking false
);

SELECT
  kp_sd_line_id,
  sale_date,
  clientsd_id,
  kp_name,
  destockage_channel,
  is_destocked_sd,
  quantity,
  total_amount
FROM @this_model
WHERE destockage_channel_conflict;
