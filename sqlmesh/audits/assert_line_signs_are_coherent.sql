/*
  Zero lines, sign mismatches, and returns in channels that do not have them.

  Replaces the blanket accepted_range(quantity, min_v := 0, inclusive := false)
  and the matching one on total_amount, which cannot express the actual rule:

    GMS accepts returns -- a negative quantity with a negative amount is a
    real transaction there.
    Traditional trade does not. A negative in TT is a credit note that was
    never processed, or a typo, and it silently reduces revenue.

  Three conditions, each a different problem:

    1. quantity = 0 or total_amount = 0
       Meaningless in any channel. A line that moved nothing and was worth
       nothing is a placeholder row someone forgot to delete.

    2. negative outside GMS
       A return recorded where returns do not happen.

    3. SIGN(quantity) <> SIGN(total_amount)
       The one that matters most and is invisible otherwise: a return entered
       with a negative quantity but a POSITIVE amount adds revenue while
       removing stock. Both numbers look plausible on their own.

  Blocking. Unlike a price disagreement, none of these has a defensible
  downstream interpretation.
*/
AUDIT (
  name assert_line_signs_are_coherent,
  dialect duckdb
);

SELECT
  sales_line_id,
  sale_date,
  sku,
  price_tier,
  sales_channel,
  salesperson_id,
  clientsd_id,
  quantity,
  total_amount,
  CASE
    WHEN quantity = 0 OR total_amount = 0 THEN 'zero line'
    WHEN SIGN(quantity) <> SIGN(total_amount) THEN 'sign mismatch'
    ELSE 'return outside GMS'
  END AS issue
FROM @this_model
WHERE quantity = 0
   OR total_amount = 0
   OR (price_tier <> 'GMS' AND (quantity < 0 OR total_amount < 0))
   OR SIGN(quantity) <> SIGN(total_amount);
