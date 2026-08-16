# Historised dimensions, and the Excel lookups that feed them

## Why By Time failed, and what to use instead

SQLMesh's `SCD_TYPE_2_BY_TIME` expects the source to be a **snapshot**: one row
per key, with an `updated_at` saying when that state began. It detects change by
comparing the snapshot against what is already stored.

`stg_salesteam_data` and `stg_clientsd_data` are the opposite — **full dated
history**, several rows per key. By Time sees duplicate unique keys and cannot
decide which is current. That is why it did not work.

⚠️ **`dim_products` works on By Time only because `Ref_Products` has no history
yet.** One row per SKU is a valid snapshot by accident. The moment you add a
second dated row for SKU 079 — which adding the tier prices will do — it breaks
the same way. This is the immediate reason to change it now rather than later.

`SCD_TYPE_2_BY_COLUMN` with a monthly cron, `batch_size 1` and the
`effective_from` interval filter is a sound workaround: it replays history one
month at a time so each key appears once per batch. But `valid_from` comes from
the **execution time**, not the business date, so correctness depends on change
dates lining up with cron boundaries — and it cannot be rebuilt from scratch
without replaying every interval in order.

### LEAD windows, `kind FULL`

SCD Type 2 machinery exists to *detect* change in a snapshot. Your source
already **is** the history — nothing needs detecting, only windowing:

```sql
effective_from AS valid_from,
LEAD(effective_from) OVER (PARTITION BY sd_id ORDER BY effective_from) AS valid_to
```

`valid_from` is always the business date. `valid_to` is the next change, NULL
for the current version. Deterministic, backfill-safe, independent of when the
pipeline runs, reproducible from source at any time, and no cron alignment to
maintain.

Verified against your actual case: Nzongue Charmita moving from traditional
trade to GMS resolves to TT for a February sale and GMS for a November one,
with no window overlap.

All four dimensions now use it — `dim_salesperson`, `dim_clientsd`,
`dim_products`, `dim_product_price`. `assert_no_overlapping_scd_windows` stays
on and matters more, since SQLMesh is no longer managing the windows.

Surrogate keys are unchanged: still `hash(business_key, effective_from)`. So
existing fact rows keep resolving, and an SD that goes destocké → non-destocké
→ destocké gets three distinct keys rather than two colliding ones.

**Note the cron drops from `@monthly` to `@daily`.** The monthly cron existed to
make By Column's batching line up with month-start changes. With LEAD there is
nothing to line up, and a correction to a historical `effective_from` now takes
effect on the next run instead of waiting for a month boundary.

---

## The Excel lookup problem

> SKU 079 went from 10000 in 08/2026 to 11000 in 09/2026. Ref_Products has two
> rows. A plain VLOOKUP returns one of them for every line, regardless of date.

Correct, and worth fixing — but **the warehouse figure is already right.**
`fact_sales` resolves `unit_price_standard` through `dim_product_price`'s SCD
window at `sale_date`, and takes `product_category`, `unit_weight_standard` and
the rest from `dim_products` at `sale_date`. The sheet's lookup columns feed
`unit_price_sheet` (traceability only) and the supervisors' own pivot tables.

So the risk is not wrong warehouse numbers. It is a supervisor whose pivot
disagrees with the report — and being unable to say which is right.
`assert_sheet_lookup_is_date_correct` measures exactly that drift.

### The formula

`LOOKUP(2, 1/(...), ...)` returns the **last** row satisfying the condition, so
with `Ref_Products` sorted ascending by `effective_from` within each SKU it
gives the latest version in force on or before the sale date. No CSE, no
dynamic arrays, works in every Excel version:

```excel
=IFERROR(
   LOOKUP(2,
     1/((Ref_Products[sku]=$F104)*(Ref_Products[effective_from]<=$D104)),
     Ref_Products[unit_price]),
   "")
```

`$F104` = the SKU cell, `$D104` = the `Sale_DATE` cell. Same pattern for
`product_name`, `unit_weight`, `product_cat` — change only the last argument.

⚠️ **Requires `Ref_Products` sorted by `(sku, effective_from)` ascending.** The
`1/(...)` trick works by scanning for the last non-error position; unsorted
rows give a wrong answer with no error. Sort the table and say so in a note on
the sheet, because this will not be obvious to whoever maintains it next.

Excel 2021 / 365 alternative, which does not depend on sort order:

```excel
=LET(k, $F104, d, $D104,
     eff, FILTER(Ref_Products[effective_from],
                 (Ref_Products[sku]=k)*(Ref_Products[effective_from]<=d), ""),
     val, FILTER(Ref_Products[unit_price],
                 (Ref_Products[sku]=k)*(Ref_Products[effective_from]<=d), ""),
     IFERROR(INDEX(val, MATCH(MAX(eff), eff, 0)), ""))
```

Slower over 37,000 rows, but robust to sort order. Given the file sizes you are
already seeing, prefer the `LOOKUP` form and enforce the sort.

### Two guards worth adding to the workbook

1. **A blank `effective_from` breaks both formulas silently** — the row is
   simply never matched. `stg_products_data` already drops rows where
   `effective_from` fails to cast, so a blank means a product vanishes from
   `dim_products` and every line for it becomes an orphan. Make it a required,
   non-blank, date-validated cell.
2. **Give the first tier prices the same `effective_from` as the existing
   product row**, not today's date. Otherwise every historical line joins to a
   window that has no tier price and `unit_price_standard` comes back NULL.
