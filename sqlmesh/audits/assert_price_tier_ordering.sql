/*
  Sell-in must not cost more than traditional trade.

  SD is one step further up the distribution chain than TT: the KP sells to the
  sub-distributor below the price the salesperson charges the market. An SD
  price above TT is a data-entry inversion -- most often the two columns filled
  in the wrong order -- and it is invisible without this check, because each
  price on its own is a perfectly plausible number. It then propagates into
  every sell-in vs sell-out margin as a negative.

  ⚠ GMS IS DELIBERATELY NOT COMPARED AGAINST TT.
  An earlier version also flagged gms > tt and fired on 14 rows. That rule
  assumed unit_price_gms is what the GMS team PAYS (which would sit near the SD
  price, below TT). If it is instead what the GMS channel CHARGES -- a
  supermarket shelf price -- then above TT is entirely normal and the audit was
  flagging correct data. Confirm which it is; if it is a purchase price, add
  the GMS clause back with the same shape as the SD one.

  Re-pivots @this_model, which is the long price table, so the tiers for one
  (sku, effective_from) can be compared on a single row.

  blocking false: a promotional SD price above TT is unusual, not impossible.
*/
AUDIT (
  name assert_price_tier_ordering,
  dialect duckdb,
  blocking false
);

SELECT
  sku,
  effective_from,
  MAX(unit_price) FILTER (WHERE price_tier = 'TT')  AS price_tt,
  MAX(unit_price) FILTER (WHERE price_tier = 'GMS') AS price_gms,
  MAX(unit_price) FILTER (WHERE price_tier = 'SD')  AS price_sd
FROM @this_model
GROUP BY sku, effective_from
HAVING MAX(unit_price) FILTER (WHERE price_tier = 'SD')
       > MAX(unit_price) FILTER (WHERE price_tier = 'TT');