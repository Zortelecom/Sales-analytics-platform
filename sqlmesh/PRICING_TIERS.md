# Price tiers

## The diagnosis

`assert_amount_matches_qty_x_price` compares `total_amount` against
`quantity × unit_price_actual`. `unit_price_actual` is `s.unit_price` — the
`Unit_Price` column in the ExSD workbook, which is a VLOOKUP of
`Ref_Products`, sitting in the same row as `Product_name`, `Product_Cat` and
`Unit_Weight`, all from the same lookup.

**It is not the price the line was transacted at.** It is the traditional-trade
reference price copied into the sheet. So:

- the audit fires wherever the real price differs from TT — every GMS line and
  every sell-in line — flagging correct data as broken
- `price_variance_pct` computes `(s.unit_price - p.unit_price) / p.unit_price`
  with both sides sourced from `Ref_Products`, so it is ~0 by construction
- the only actual price in the data is `total_amount / quantity`

Removing the audit would have been the wrong fix: it would have removed the
one check that catches a supervisor mistyping an amount, in order to silence a
false positive caused by a mislabelled column.

## The shape

Your instinct — extra price columns on `Ref_Products` — is right for the
workbook. It is wrong for the models. So do both, with an unpivot between:

```
Ref_Products (wide, 3 columns)      what a supervisor can maintain
        │  UNPIVOT
        ▼
staging.stg_product_prices (long)   one row per (sku, price_tier, effective_from)
        ▼
marts.dim_product_price             SCD2 per (sku, price_tier)
        ▼
fact_sales / fact_kp_sd             joined at the line's own tier
```

A fourth tier later becomes rows, not a schema change. Each tier's price
history moves independently — TT can rise while SD holds. And a product not
sold through a channel is simply absent for that tier rather than present with
NULL, which distinguishes "not sold here" from "price missing".

## Excel change

Add two columns to `Ref_Products`, after the existing `unit_price`:

| Column | Meaning |
|---|---|
| `unit_price` | traditional trade — salesperson to market. **Unchanged.** |
| `unit_price_gms` | GMS — grande et moyenne surface |
| `unit_price_sd` | KP to sub-distributor (sell-in) |

Leave a cell **blank**, not zero, where a product has no price for a tier.
Blank means "not sold through this channel"; zero would fail the
`accepted_range(min_v := 1)` audit, which is the correct treatment of a zero
price but the wrong message for an absent one.

Then add both to `expected_columns` for `ref_products` in
`ingestion/contracts/contracts.yaml`, otherwise they land fine but log as
schema drift on every run.

⚠️ **Back-date the first row.** `Ref_Products` is SCD2 on `effective_from`. If
the GMS and SD prices are added with today's date, every historical line joins
to a version with no tier price and `unit_price_standard` comes back NULL. Give
the first tier prices the same `effective_from` as the existing product row.

## Three prices, not two

`fact_sales` and `fact_kp_sd` now carry:

| Column | What it is |
|---|---|
| `unit_price_sheet` | what the workbook says — the TT lookup. Kept for traceability only. |
| `unit_price_effective` | `total_amount / quantity`. **The actual price.** Every variance measure should use this. |
| `unit_price_standard` | reference price for this line's tier and date, from `dim_product_price` |

`price_variance_pct` is now effective vs the tier standard, which is what
anyone reading "price variance" assumes it means.

⚠️ **This changes the Power BI semantic model.** `unit_price_actual` no longer
exists; measures referencing it need repointing at `unit_price_effective`. It
also unblocks `Weighted Avg Price Variance %`, which could not be built while
both sides of the comparison came from the same source column.

## One audit became two

They were answering different questions under one name.

**`assert_amount_matches_qty_x_price`** — blocking, TT lines only. Does the
workbook's own quantity × price equal its own amount? A supervisor who types an
amount inconsistent with their own figures has made an arithmetic error. Scoped
to TT because that is the only tier where the sheet's price is the right
comparison.

**`assert_price_matches_tier`** (and `_kp`) — non-blocking, 5% tolerance. Is the
effective price close to the reference for that tier and date? Negotiated
discounts, promotions and rounding all live here, and none is a reason to
refuse to build a fact. **What matters is the distribution.** A handful of lines
is commercial reality. A whole salesperson, a whole SD, or a whole month is
either a price list nobody updated or an SD being billed off-list — and that
second one is precisely the kind of quantifiable defect a sell-in/sell-out
reconciliation exists to find.

A NULL `unit_price_standard` is included rather than filtered: it means a
product is being sold through a channel with no price list, which is a
master-data gap worth seeing.

## Tier assignment

`fact_sales` derives the tier from `dim_salesperson.sales_channel`:

```sql
CASE WHEN UPPER(TRIM(sp.sales_channel)) = 'GMS' THEN 'GMS' ELSE 'TT' END
```

`fact_kp_sd` is always `'SD'`.

⚠️ Confirm the literal against your actual `sales_channel` values. If more
channels acquire their own price lists, move this into a reference table rather
than growing the CASE — the same argument that made the long price table right.

## Checking it worked

```sql
-- every tier has prices, and none is empty
SELECT price_tier, COUNT(*) AS rows, COUNT(DISTINCT sku) AS skus,
       MIN(unit_price), MAX(unit_price)
FROM marts.dim_product_price GROUP BY 1;

-- GMS should no longer dominate the variance failures
SELECT price_tier, sales_channel,
       COUNT(*) AS lines,
       COUNT(*) FILTER (WHERE ABS(price_variance_pct) > 5) AS off_list,
       ROUND(AVG(price_variance_pct), 2) AS avg_variance
FROM marts.fact_sales GROUP BY 1, 2 ORDER BY 3 DESC;

-- sell-in should sit below TT by a consistent margin
SELECT sku,
       MAX(unit_price) FILTER (WHERE price_tier = 'TT')  AS tt,
       MAX(unit_price) FILTER (WHERE price_tier = 'GMS') AS gms,
       MAX(unit_price) FILTER (WHERE price_tier = 'SD')  AS sd
FROM marts.dim_product_price
WHERE valid_to IS NULL GROUP BY 1 ORDER BY 1;
```

The last one is worth reading carefully the first time. Any SKU where `sd > tt`
is a data-entry inversion, and it would have been invisible before.
