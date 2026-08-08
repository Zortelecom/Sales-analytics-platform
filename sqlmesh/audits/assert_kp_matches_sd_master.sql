/*
  The KP named on the line differs from the SD's KP of record.

  Today, with one KP in the data, every TRUE is an error -- most likely a
  spelling variant that kp_name_normalized could not collapse ("Henri et
  Freres" vs "Henri & Freres"), or a stale `kp` value in Ref_ClientsSD.

  Once a second KP is onboarded, sustained TRUEs for one SD mean something
  different: that SD now has two suppliers, and dim_clientsd.kp has stopped
  being a meaningful attribute for it. That is the trigger to add a Ref_KP
  table and drop kp from the SD dimension.

  blocking false -- this is a signal about master data and about the shape of
  the business, not a reason to refuse to build the fact.
*/
AUDIT (
  name assert_kp_matches_sd_master,
  dialect duckdb,
  blocking false
);

SELECT
  kp_sd_line_id,
  sale_date,
  clientsd_id,
  kp_name,
  kp_name_normalized,
  kp_of_record_sd,
  total_amount
FROM @this_model
WHERE kp_mismatch;
