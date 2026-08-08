# Improvement Plan — post KP-SD ingestion

Scope: what's needed to bring the reporting (Streamlit) and orchestration (Dagster) layers
up to date with the new KP-SD (sell-in) ingestion, plus a few concrete gaps found while
reading through the branch that are worth fixing regardless of this specific feature.

Legend: 🔴 Critical · 🟠 High · 🟡 Medium · 🟢 Nice-to-have

---

## 🔴 Critical — silent-drop / correctness risks

These directly contradict the project's own stated principle: *never silently drop data,
prefer a loud failure or a `needs_review`-style flag over a quiet gap.*

1. **KP-native SKU mapping exists but is never used.**
   `stg_kp_sku_mapping.sql` and `Ref_sku_mapping` were built specifically to translate a
   KP's own SKU codes to the platform's internal `sku`. But `stg_kp_sd_data.sql` selects
   `sku` straight from the raw KP file and `fact_kp_sd.sql` joins directly on
   `s.sku = p.sku` — the mapping model is never joined in anywhere. If any KP uses its own
   SKU codes (which is presumably the entire reason the mapping table exists), every one of
   those rows will fail `assert_no_orphaned_product_kp` (`product_key IS NULL`) instead of
   resolving correctly.
   **Fix:** join `stg_kp_sku_mapping` into `stg_kp_sd_data` (or a new intermediate step)
   before the SKU is used to join `dim_products`, e.g.
   `LEFT JOIN staging.stg_kp_sku_mapping m ON raw.sku = m.kp_sku` then
   `COALESCE(m.internal_sku, raw.sku) AS sku`.

2. **`assert_no_orphaned_client_kp` exists but is commented out.**
   The audit file is present and correct, but `fact_kp_sd.sql`'s `audits()` block has it
   commented out (`-- , assert_no_orphaned_client_kp`), so a KP-SD row referencing an SD
   that doesn't resolve in `dim_clientsd` will pass silently.
   **Fix:** uncomment it once (1) is fixed and any expected transient mismatches are
   understood, or keep it commented but add a clear `TODO` with a tracking date — right
   now it reads like an oversight, not a decision. The Dagster-side check added in this
   round (`data_quality_full_report_kp_sd`) already runs the same query so it's visible in
   the meantime, but the SQLMesh-level gate is the one that should actually block/flag
   before promotion to prod.

3. **`assert_no_unmapped_kp_sku` is defined but attached to no model at all.**
   It isn't referenced — not even commented out — anywhere. Same category as (2), but
   further from being caught: nothing currently signals that this file exists and does
   nothing. Once (1) is fixed, this audit should be attached to `stg_kp_sd_data` (checking
   against `stg_kp_sku_mapping`, not `dim_products` directly — the current query is written
   as if `sku` were already the internal code).

4. **`references_seeds` (Dagster asset) silently drops the KP-SKU-mapping seed.**
   `ReferenceExtractor.read()` returns a `ref_kp_sku_mapping` key and the standalone
   `ingestion/main.py` CLI path writes it to `kp_sku_mapping_data.csv` correctly. But
   `orchestration/assets/ingestion.py`'s `references_seeds()` asset has its own
   `seed_mapping` dict, and it was never updated to include `'ref_kp_sku_mapping':
   'kp_sku_mapping_data'` — so running ingestion via **Dagster** never writes that seed,
   while running it via the CLI does. `raw_kp_sku_mapping_data.sql` (a `SEED` model
   pointing at that CSV) will fail to load with file-not-found the first time someone
   runs the full pipeline through Dagster on a clean checkout.
   **Fix included in this round** — see `orchestration/assets/ingestion.py` in the
   deliverables; the dict now includes the missing entry.

---

## 🟠 High — orchestration/reporting parity with the new source

The KP-SD ingestion pipeline (extractor → seed → raw → staging → mart) is complete, but
neither Dagster nor Streamlit knew about it before this round. Applied in this round:

5. **No Dagster asset extracted KP-SD data.** Added `kp_sd_seed` (mirrors `sales_seed`,
   writes both `kp_sd_destocke_data.csv` and `kp_sd_non_destocke_data.csv`), wired into
   `seeds_metadata`'s `ins` and into `definitions.py`'s `assets` list.
   **⚠️ Also add `kp_sd_seed` to `orchestration/assets/__init__.py`'s exports** — that file
   wasn't in scope for this review, but `definitions.py` imports `kp_sd_seed` from
   `orchestration.assets`, so the package `__init__.py` needs the matching `from
   .ingestion import ..., kp_sd_seed` line or the import will fail.

6. **`preprocessing.py` didn't know about the `kp_sd` source type.** It hardcodes
   `delete_pattern` / `required_sheets` per `source_type` in Python rather than reading
   `ingestion/config/sources.yaml`'s `processing:` block — so adding a `kp_sd:` entry to
   `sources.yaml` alone did nothing for the Dagster path. Added the matching `elif
   source_type == "kp_sd"` branch. **Longer term:** consider having `preprocessing.py`
   read `sources.yaml`'s `processing:` block directly instead of hand-mirroring it in two
   places — the two are already one edit away from silently diverging again.

7. **No data-quality asset check covered `fact_kp_sd`.** `data_quality_full_report` was
   bound only to `fact_sales`. Added a parallel `data_quality_full_report_kp_sd` check
   bound to `fact_kp_sd`, covering not-null, negative-amount, orphaned-product,
   orphaned-client (see 🔴 #2), amount-vs-qty×price, integer-XAF, and unmapped-SKU (see
   🔴 #3) — all writing to the same `bi.quality_trend` table with a `kp_sd_`-prefixed
   audit name so the existing Quality Trends Streamlit page picks them up with no changes.

8. **No BI views exposed `fact_kp_sd`.** Added four views to `serving/templates/bi_views.sql`:
   `v_kp_sd_base` (denormalized), `v_kp_sd_monthly_kpi`, `v_kp_performance_kpi`, and
   `v_sellin_sellout_kpi` (the reconciliation view joining sell-in against `v_client_kpi`'s
   sell-out, at month × SD grain).

9. **No Streamlit page surfaced sell-in data.** Added
   `reporting/pages/8_Sell_In_Sell_Out.py` and registered it in `app.py`'s nav. It's
   written as a self-contained page (own DuckDB connection) because `reporting/utils/`
   wasn't available to inspect while writing it — **please swap its connection logic for
   the existing helper** (likely `reporting/utils/db.py` or similar) so it shares cache
   behavior and any filter-persistence pattern with the other pages once that's wired up
   (see item 13 below).

10. **Verify `serving/sync.py`'s table discovery.** Not reviewed in this round (the file
    wasn't provided). If it enumerates the `marts` schema dynamically, `fact_kp_sd` will
    sync automatically. If it maintains an explicit table allowlist, `fact_kp_sd` needs to
    be added there or it will never reach the serving DB even though every other layer is
    ready.

---

## 🟡 Medium — data-model consistency worth double-checking

11. **`dim_clientsd`'s SCD2 logic moved from staging to gold.** `stg_clientsd_data` is now
    `FULL` (plain dated history) and `dim_clientsd` itself is `SCD_TYPE_2_BY_COLUMN`,
    driven by `effective_from`. This is a reasonable simplification, but it means any other
    model or report still assuming `stg_clientsd_data` carries `valid_from`/`valid_to`
    (the old shape) will break. Worth a repo-wide grep for `stg_clientsd_data` before the
    next promotion to confirm nothing downstream still expects the old staging-level SCD2
    columns.

12. **`data_quality.py`'s schema-naming assumption.** Every query in this file hardcodes
    `f"marts__{env}"` / `f"staging__{env}"` / (now) `f"raw__{env}"`, but
    `orchestration/assets/transformation.py`'s `marts_validation` asset explicitly branches
    on environment: `"marts" if environment == "prod" else f"marts__{environment}"`. If
    `data_quality_full_report[_kp_sd]` is ever run with `SQLMESH_ENV=prod`, its queries will
    point at a schema (`marts__prod`) that doesn't exist. Not introduced by this round, but
    it now affects the new KP-SD check too — worth fixing once, in one helper function,
    rather than patching every query.

13. **Destocked-flag mismatch signal is new and not yet acted on.** `v_kp_sd_base` computes
    `destocked_flag_mismatch` (SD master flag vs. which workbook a transaction was actually
    found in) and the new Streamlit page surfaces it as a passive warning. Decide whether
    this should also feed the At-Risk/Alerts dashboard already on the roadmap (see below),
    since it's the same kind of "needs a human to look at it" signal.

---

## 🟢 Carried over from before this round (still open)

Streamlit:
- Filter persistence via `st.session_state` (the new page above doesn't have this either —
  fold it in when this lands for the rest of the app, rather than adding a second pattern)
- Active filter breadcrumbs
- Pace-to-target KPI card
- Client Performance page (`v_client_kpi` already exists and is now also consumed by
  `v_sellin_sellout_kpi` above)
- Rep Target Attainment page
- At-Risk/Alerts dashboard — a natural home for both the promo-impact interim report
  (`rep_promo_impact_interim`, already landed) and the destocked-flag-mismatch signal
  (item 13 above)

Code quality:
- Fix `available_channels()` to query from `fact_sales` rather than `dim_salesperson`
  (note: KP-SD doesn't have an equivalent "channel" column the same way — `fact_kp_sd`'s
  channel is really "which workbook", captured as `source_asserted_destocked` — keep that
  distinct from `fact_sales.sales_channel` rather than merging the two concepts)
- Extract `render_page_header()`
- Consolidate filter scalar logic
- Remove unused `DB_VIEWS_SCHEMA` constant
- Add last-refreshed timestamp tied to cache TTL

---

## Suggested order of work

1. Fix the KP-SKU mapping join (🔴 #1) — everything downstream of it (audits, the
   unmapped-SKU check, `assert_no_orphaned_product_kp` false failures) depends on this
   being correct first.
2. Fix the `references_seeds` seed-mapping bug (🔴 #4) and add `kp_sd_seed` to
   `orchestration/assets/__init__.py` (🟠 #5) — both are one-line, both currently break a
   clean-checkout Dagster run.
3. Re-enable `assert_no_orphaned_client_kp` and attach `assert_no_unmapped_kp_sku` to a
   model (🔴 #2, #3), now that #1 makes the SKU side trustworthy.
4. Verify `serving/sync.py` picks up `fact_kp_sd` (🟠 #10) — without this, none of the new
   BI views or the new Streamlit page will have data to show.
5. Land the BI views and Streamlit page (already drafted in this round, items 8–9) and
   confirm the numbers reconcile against `fact_promo_gains_actual` / a known KP invoice for
   at least one SD before trusting the sell-through ratio in front of anyone.
6. Everything in 🟡 and 🟢 can proceed in parallel / opportunistically.
