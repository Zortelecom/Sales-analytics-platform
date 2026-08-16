/*
  Sell-in lines that cannot be right at any severity: zero lines and sign
  mismatches.

  (2026-08) NEGATIVES REMOVED FROM THIS AUDIT.
  An SD can return stock to a KP, exactly as a GMS client can return to an SD,
  so a negative quantity paired with a negative amount is a real transaction
  here. It is reported by assert_no_negative_sellin instead, which is
  non-blocking.

  What stays blocking:

    1. quantity = 0 or total_amount = 0
       Meaningless in either direction. A line that moved nothing and was
       worth nothing is a placeholder someone forgot to delete.

    2. SIGN(quantity) <> SIGN(total_amount)
       The one that matters. A return with a negative quantity but a POSITIVE
       amount adds revenue while removing stock, and both numbers look
       plausible in isolation. This is what caught an SD table whose
       total_amount was computed from another SD sheet's unit_price and qty --
       a cross-sheet formula reference, invisible to every aggregate.

  Now that negatives are expected, the sign check is doing more work than
  before, not less: previously a wrong-signed return was caught twice.
*/
AUDIT (
  name assert_line_signs_are_coherent_kp,
  dialect duckdb
);

SELECT
  kp_sd_line_id,
  sale_date,
  sku,
  kp_name,
  clientsd_id,
  quantity,
  total_amount,
  CASE
    WHEN quantity = 0 OR total_amount = 0 THEN 'zero line'
    ELSE 'sign mismatch'
  END AS issue
FROM @this_model
WHERE quantity = 0
   OR total_amount = 0
   OR SIGN(quantity) <> SIGN(total_amount);