/*
  Returns from a sub-distributor back to a Key Player.

  Legitimate -- an SD returns stock to a KP the same way a GMS client returns
  to an SD -- so this is non-blocking. It exists because the VOLUME matters
  even when each row is valid:

    * a spike in returns for one SD is a commercial signal (overstocking, a
      product that will not move, a promotion that missed)
    * returns inflate gross sell-in while cancelling net sell-in, so any
      sell-through ratio computed on gross figures drifts as returns grow
    * a return recorded in the wrong month reverses revenue in a period that
      never shipped it

  Sign coherence is checked separately and blockingly by
  assert_line_signs_are_coherent_kp: a negative quantity must carry a negative
  amount.

  Rates per SD are aggregated in reports.rep_kp_sd_dq_metrics.
*/
AUDIT (
  name assert_no_negative_sellin,
  dialect duckdb,
  blocking false
);

SELECT
  kp_sd_line_id,
  sale_date,
  sku,
  kp_name,
  clientsd_id,
  destockage_channel,
  quantity,
  total_amount
FROM @this_model
WHERE quantity < 0
   OR total_amount < 0;
