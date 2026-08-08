# Reporting Layer — Review & Improvement Plan

Scope: `reporting/` (Streamlit multi-page app) — `utils/`, `components/`, `pages/`.
Not in scope: SQLMesh models, ingestion, orchestration, serving layer internals
(only touched where the reporting layer's assumptions about them matter).

Files reviewed: `db.py`, `queries.py`, `views.py`, `filters.py`, `formatters.py`,
`schema_context.py`, `ask.py`, `kpi_cards.py`, `ranking_table.py`, `charts.py`,
`config.py`, and pages 1–8 (`1_Executive_Overview.py` … `8_Sell_In_Sell_Out.py`).

Legend: 🔧 = fixed in this pass (file attached) · 🔎 = flagged, needs a decision
or confirmation against `bi_views.sql` before changing.

---

## Critical

### C1. 🔧 "Ask Your Data" safety check was defined but never called
**Files:** `utils/ask.py`, `pages/7_Ask_Data.py`

`is_safe_sql()` existed and looked like a guard rail, but nothing in
`7_Ask_Data.py` ever called it — the LLM's generated SQL went straight to
`db_query(sql)` unchecked. On top of that, the guard itself was thin: it
blocked a keyword list but not stacked statements (`SELECT ...; DROP ...`)
or DuckDB table functions that read the filesystem directly
(`read_csv`, `read_parquet`, `glob`, …), which a **read-only DB connection
does not stop**, since they don't go through the database at all.

**Fix applied:**
- `7_Ask_Data.py` now calls `is_safe_sql(sql)` and stops with an error if it
  fails, before ever touching `db_query`.
- `is_safe_sql()` now also rejects: stacked statements (`;` after stripping
  a trailing one), anything not starting with `SELECT`/`WITH`, and the
  filesystem/network-reading table functions above.
- Added `enforce_row_limit()`, which wraps the query in
  `SELECT * FROM (...) LIMIT 500` so the "500 rows max" rule in the system
  prompt is actually guaranteed instead of merely requested.

**Residual risk (documented in-file, not fixed here):** this is
defence-in-depth for a trusted single-user tool. If "Ask Your Data" is ever
exposed to less-trusted users, the real fix is a least-privilege DB role
scoped to the `bi`/views schema, not string-matching SQL.

---

## High

### H1. 🔧 Section headers rendered a literal stray string on every page
**File:** `components/kpi_cards.py` — `render_section_header()`

```
{sub_html}hbtt
</div>
```

A leftover typo meant every section header on every page (Regional,
Salesforce, Product, Time Intelligence, …) rendered the literal text
`hbtt` beneath its title. Removed. While in there, also escaped
`title`/`subtitle` with `html.escape()` for consistency with `_card_html()`,
which already escapes its inputs.

### H2. 🔧 Quarterly fallback used calendar quarters, not fiscal quarters
**File:** `utils/queries.py` — `get_quarterly_summary()`

The fiscal year in this project starts in October (`filters.py`'s
`QUARTER_MONTHS = {1: [10,11,12], 2: [1,2,3], 3: [4,5,6], 4: [7,8,9]}`), and
`v_quarterly_kpi` is built from `dim_date`'s fiscal quarter mapping. But the
**fallback** path (used whenever `v_quarterly_kpi` is missing) computed
`CEIL(month / 3.0)` — plain calendar quarters. An October sale would be
fiscal Q1 via the normal path, but Q4 via the fallback — silently
inconsistent the moment the view disappears, with no error or warning.

**Fix applied:** the fallback now re-indexes months so fiscal Oct=1 … Sep=12
before taking `CEIL(.../3.0)`, matching `QUARTER_MONTHS` exactly.

### H3. 🔎 `queries.py` doesn't use the centralized view constants it has
**Files:** `utils/queries.py`, `utils/views.py`

`views.py` exists specifically so table names stay schema-qualified and in
sync with `bi_views.sql` (`SALES_BASE = f"{_S}.v_sales_base"`, etc.), but
**no query builder in `queries.py` imports it** — every one of the ~30
queries uses a bare, unqualified name (`FROM v_monthly_kpi`, `FROM
v_sales_base`, …). This works today only because `DB_VIEWS_SCHEMA` defaults
to `"main"`, DuckDB's default search-path schema. If `SERVING_DB_VIEWS_SCHEMA`
is ever overridden in an environment, every single query in this file breaks
at once, silently inconsistent with the one file that was built to prevent
exactly that.

**Not changed in this pass** — this is a ~30-callsite mechanical refactor
across a file with no test harness available in this review, and a
find-and-replace across many multi-line f-strings is exactly the kind of
change that's easy to get subtly wrong without being able to run it. It
would need to:
1. Add `from reporting.utils.views import MONTHLY_KPI, SALES_BASE, WEEKLY_KPI, QUARTERLY_KPI` at the top of `queries.py`.
2. Replace each bare `FROM v_x` / `WHERE ... FROM v_x` with the imported constant.
3. Run `sqlmesh test`-equivalent coverage (or at minimum `streamlit run` every page) against a real `serving_dev.db` before merging.

Recommend doing this as its own PR with the existing pages as manual test
coverage, rather than folding it into an unrelated change.

---

## Medium

### M1. 🔧 KP-SD page bypassed the view-name centralization pattern
**Files:** `pages/8_Sell_In_Sell_Out.py`, `utils/views.py`

The new sell-in/sell-out page hardcoded `f"{DB_VIEWS_SCHEMA}.v_sellin_sellout_kpi"`
and `f"{DB_VIEWS_SCHEMA}.v_kp_sd_base"` inline instead of using `views.py`,
because `views.py` simply didn't have constants for the four new KP-SD views
yet (`v_kp_sd_base`, `v_kp_sd_monthly_kpi`, `v_kp_performance_kpi`,
`v_sellin_sellout_kpi`).

**Fix applied:** added the four missing constants to `views.py` and switched
the page to import them (`KP_SD_BASE`, `SELLIN_SELLOUT_KPI`).

### M2. 🔎 Possible column-naming mismatch on the KP-SD page
**File:** `pages/8_Sell_In_Sell_Out.py`

`v_sellin_sellout_kpi` is queried with unqualified `year`/`month` columns —
the convention this codebase uses for aggregate KPI views like
`v_monthly_kpi` and (per the fallback in `get_quarterly_summary`)
`v_quarterly_kpi`. But `v_kp_sd_base`, queried a few lines later in the same
page, uses `sale_year`/`sale_month` — the convention used by raw base views
like `v_sales_base` and `v_weekly_kpi`.

Both conventions are genuinely in use elsewhere in this codebase, so this
isn't necessarily a bug — but it's worth 30 seconds against `bi_views.sql`
to confirm `v_sellin_sellout_kpi`'s actual column names match what the page
queries. If it turns out to expose `sale_year`/`sale_month` instead, the two
`query(...)` calls near the top of the page (the `years` and `months`
lookups, plus the `where`/`params` construction) need the column names
updated. Left a `VERIFY` comment in the file pointing here.

### M3. 🔎 Day-of-week convention unverified in the seasonality heatmap
**File:** `pages/5_Time_Intelligence.py`

```python
DAY_NAMES = {0:"Mon", 1:"Tue", 2:"Wed", 3:"Thu", 4:"Fri", 5:"Sat", 6:"Sun"}
```

This assumes `day_of_week` (from `v_sales_base`) is `0 = Monday`. DuckDB's
own `dayofweek()` returns `0 = Sunday`; its `isodow()` returns `1 = Monday`.
If the SQLMesh model populated `day_of_week` with `dayofweek()` (the more
common default), every day in this heatmap is shifted by one — Sundays would
show as "Mon". Not changed here since it depends on a staging-layer SQL
expression outside the reporting layer's scope; worth a quick check against
the model that produces `v_sales_base.day_of_week`.

### M4. 🔧 "Ask Your Data" has no visibility into sell-in/sell-out data
**File:** `utils/schema_context.py`

The schema description sent to the LLM only listed sell-out views
(`v_monthly_kpi`, `v_sales_base`, `v_weekly_kpi`) plus two dimension tables.
Questions like *"how much did KP X ship to SD Y last month"* had no chance
of being answered correctly — the model was never told those views exist.

**Fix applied:** added `v_kp_sd_base` and `v_sellin_sellout_kpi` to the
schema context, with grain/column descriptions in the same style as the
existing entries, and switched to importing the schema-qualified names from
`views.py` instead of hand-qualifying them inline.

### M5. 🔧 `get_schema_context()` had no caching
**File:** `utils/schema_context.py`

Every question typed into "Ask Your Data" re-ran 5 separate
`SELECT * FROM ... LIMIT 2` sample queries before the LLM call even started
— pure added latency for data that doesn't change within a session (or
really, within the 5-minute `db.query()` cache window everywhere else in
the app). Wrapped in `@st.cache_data(ttl=600, show_spinner=False)`, matching
the convention already used in `db.py`.

---

## Low / Nice-to-have

### L1. 🔧 Destocked-flag-mismatch check ignored the month filter
**File:** `pages/8_Sell_In_Sell_Out.py`

The main reconciliation table respects the Year + Month sidebar filters, but
the mismatch-review query below it only ever filtered on `sale_year`, so
picking a specific month still showed mismatches for the whole year. Fixed
to add `AND sale_month = ?` when a specific month is selected.

### L2. Model version pinned in `ask.py` is dated
**File:** `utils/ask.py`

Both LLM calls hardcode `model="claude-sonnet-4-20250514"`. Anthropic's
current lineup includes Claude Sonnet 5 (`claude-sonnet-5`). Not changed
here — swapping models is a deliberate cost/latency/quality tradeoff for
you to make, not something to change silently in a bug-fix pass — but worth
a conscious upgrade decision rather than leaving it pinned to an old
snapshot indefinitely.

### L3. Confusing double-message on query errors in "Ask Your Data"
**File:** `pages/7_Ask_Data.py`, `utils/db.py`

`db.py`'s `query()` already catches `duckdb.Error` internally, shows
`st.error(...)`, and returns an **empty** DataFrame rather than raising. The
page's own `try/except` around `db_query()` therefore rarely fires for SQL
errors — instead, execution falls through to `if df.empty: st.info("The
query returned no rows...")`, so a real SQL error shows both a red error box
*and* a misleading "no rows" message. Consider having `query()` optionally
signal "errored" vs. "genuinely empty" (e.g. return `None` on error instead
of an empty DataFrame) so callers can tell the difference.

### L4. Sell-In/Sell-Out page doesn't share the standard page chrome
**File:** `pages/8_Sell_In_Sell_Out.py`

Every other page calls `render_sidebar_filters(...)`, getting the same
sidebar styling, meeting-mode selector, and data-freshness footer. Page 8
builds its own ad hoc year/month selectors directly in the sidebar instead.
Understandable for a fast-follow page per the README's own note that "the
reporting … layer[s] have not been updated for this new source yet" — but
worth folding into the standard filter/chrome pattern once the KP-SD
reporting story is fleshed out further (e.g. once `dim_clientsd`'s region
attribute is exposed for a region filter here too).

### L5. Minor code-quality nits (no functional impact)
- `components/charts.py` — `waterfall_chart()` accepts a `change_col`
  parameter that's never used in the function body; either wire it in (e.g.
  annotate bars with WoW %) or drop it.
- `pages/6_Quality_Trends.py` — uses pandas' `Styler.applymap()`, deprecated
  in favor of `Styler.map()` in pandas ≥ 2.1. Still works; will need updating
  eventually.
- `utils/db.py` — `get_connection()` never closes/disposes connections; fine
  for Streamlit's process lifetime today, just worth a comment noting it's
  intentional rather than an oversight, if a future contributor wonders.

---

## Suggested follow-ups (beyond bug-fixing)

- **Tests.** Ingestion, transformation, and serving all have test coverage
  per the README; the reporting layer has none. Even light coverage for
  `formatters.py` (pure functions, trivial to test) and `queries.py`'s
  `_build_filters()` (string-building logic that's easy to regress) would
  catch issues like H2 automatically instead of by inspection.
- **Rate/cost control on "Ask Your Data."** Each question fires two
  Anthropic API calls (`text_to_sql` + `interpret_result`) with no
  rate-limiting or per-session cap. Fine for a trusted internal tool; worth
  a guard if this page is ever opened up more broadly.
- **Region dimension.** Per your notes, the region dimension is designed
  but not yet wired into the Power BI scope. Once it lands, `filters.py`'s
  `available_regions()` (currently sourced from `dim_salesperson` only)
  and the KP-SD page could both use it for a consistent region filter
  across sell-in and sell-out.
- **`fact_targets` grain.** Since targets are salesperson × category × month
  with no SKU/client/quantity dimension, double-check that none of the
  product- or client-level "achievement %" figures in `4_Product_Performance.py`
  are implicitly allocating a coarser-grained target down to a finer grain
  it wasn't actually set at (this affects Power BI too, per your existing
  `ISCROSSFILTERED()` guard notes there — the same fan-out risk exists here
  in the DuckDB queries even without DAX).

---

## Summary table

| ID | Priority | File(s) | Status |
|----|----------|---------|--------|
| C1 | Critical | `ask.py`, `7_Ask_Data.py` | 🔧 Fixed |
| H1 | High | `kpi_cards.py` | 🔧 Fixed |
| H2 | High | `queries.py` | 🔧 Fixed |
| H3 | High | `queries.py`, `views.py` | 🔎 Flagged — recommend separate PR |
| M1 | Medium | `views.py`, `8_Sell_In_Sell_Out.py` | 🔧 Fixed |
| M2 | Medium | `8_Sell_In_Sell_Out.py` | 🔎 Flagged — verify against `bi_views.sql` |
| M3 | Medium | `5_Time_Intelligence.py` | 🔎 Flagged — verify `day_of_week` convention |
| M4 | Medium | `schema_context.py` | 🔧 Fixed |
| M5 | Medium | `schema_context.py` | 🔧 Fixed |
| L1 | Low | `8_Sell_In_Sell_Out.py` | 🔧 Fixed |
| L2 | Low | `ask.py` | 🔎 Flagged — deliberate decision needed |
| L3 | Low | `7_Ask_Data.py`, `db.py` | 🔎 Flagged |
| L4 | Low | `8_Sell_In_Sell_Out.py` | 🔎 Flagged |
| L5 | Nice-to-have | `charts.py`, `6_Quality_Trends.py`, `db.py` | 🔎 Flagged |
