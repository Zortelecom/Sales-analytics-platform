"""
reporting/app_pages/6_Sell_In_Sell_Out.py
KP sell-in versus SD sell-out reconciliation.

WHY THIS PAGE WAS FAILING
-------------------------
    Catalog Error: Table with name v_sellin_sellout_kpi does not exist!
    Did you mean "bi__dev.v_sellin_sellout_kpi"?
    LINE 1: SELECT DISTINCT year FROM main.v_sellin_sellout_kpi

It imported its object names from `reporting.utils.views`, whose constants
still carry a `main.` prefix left over from the serving.db era. `main` is
DuckDB's default schema in the in-memory database the app connects to -- not
the lake, which is attached under its own alias and reached through the search
path db.py sets at attach time. The view was there the whole time; the query
was looking in the wrong database.

This page was the LAST importer of reporting/utils/views.py. Nothing imports
it now, and it can be deleted.

Two further consequences of going through queries.py:

  * the period and scope filters are the shared sidebar ones, so this page
    agrees with every other page about what "January 2026" and "Yaounde" mean,
    including under a fiscal basis;
  * object names are bare, which is what rls.py matches on. Qualified names
    were not being scoped by the rewriter at all -- `main.v_sellin_sellout_kpi`
    does resolve to a bare name in predicate_for(), but any future qualified
    reference is one regex edge away from failing OPEN. Bare is the contract.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st

from reporting.components.kpi_cards import render_page_header, render_section_header
from reporting.utils.filters import period_label, render_sidebar_filters
from reporting.utils.formatters import fmt_currency, fmt_number, fmt_pct
from reporting.utils.queries import (
    get_destocked_flag_mismatches,
    get_sellin_sellout,
    get_sellin_years,
    sellin_scope_support,
)

# Channel and category do not exist on the KP-SD chain: sell-in is a movement
# between two legal entities, not a sale through a channel.
f = render_sidebar_filters(show_region=True, show_subregion=True,
                           show_channel=False, show_category=False)
period      = f["period"]
regions     = f["regions"]
subregions  = f["subregions"]

render_page_header("Sell-In / Sell-Out", period_label(f), f["meeting_type"])

# ---------------------------------------------------------------------------
# What can actually be filtered on this view
# ---------------------------------------------------------------------------
# region / subregion are marked [PATCH] on v_sellin_sellout_kpi in rls.py --
# they exist only once bi_views_rls_patch.sql has been applied and `sqlmesh
# plan` has rebuilt. Ask before filtering: a selection that is silently ignored
# is worse than one the page admits it cannot honour.
supported = sellin_scope_support()
ignored = [name for name, chosen, col in
           (("region", regions, "region"), ("subregion", subregions, "subregion"))
           if chosen and col not in supported]

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
with st.spinner("Loading sell-in data…"):
    df = get_sellin_sellout(period, subregions=subregions, regions=regions)

if df.empty:
    years = get_sellin_years(fiscal=period.fiscal)
    if not years:
        st.info(
            "No sell-in data is available yet. Run the KP-SD ingestion "
            "pipeline (`python -m ingestion.main --all`), then "
            "`sqlmesh plan` so bi.v_sellin_sellout_kpi is built."
        )
    else:
        listed = ", ".join(f"FY{y}" if period.fiscal else str(y) for y in years)
        st.info(f"No sell-in records for {period_label(f)}. "
                f"Periods with data: {listed}.")
    st.stop()

if ignored:
    st.caption(
        f"⚠ The {' and '.join(ignored)} filter is not applied here: "
        f"v_sellin_sellout_kpi does not carry that column yet. Apply "
        f"bi_views_rls_patch.sql and re-run `sqlmesh plan` to enable it."
    )

# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------
sell_in = float(df["sell_in_revenue"].sum())
sell_out = float(df["sell_out_revenue"].sum())
gap = sell_in - sell_out
ratio = (sell_out / sell_in * 100) if sell_in else None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Sell-in (KP → SD)", fmt_currency(sell_in, short=True))
c2.metric("Sell-out (SD → market)", fmt_currency(sell_out, short=True))
c3.metric("Sell-through", fmt_pct(ratio) if ratio is not None else "—")
# Positive gap = stock bought and not yet resold, i.e. sitting with the SD.
# Named rather than signed, because "-12M" reads as a loss to anyone who has
# not read the view definition.
c4.metric("Unsold at SD", fmt_currency(gap, short=True),
          help="Sell-in minus sell-out for the period. Positive means stock "
               "bought by the SD and not yet sold on -- an overstock signal, "
               "not a loss.")

st.markdown('<div class="spacer-md"></div>', unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Reconciliation table
# ---------------------------------------------------------------------------
render_section_header("Reconciliation", "By sub-distributor and product")
st.dataframe(
    df.sort_values(["sell_in_revenue", "sell_out_revenue"], ascending=False),
    use_container_width=True,
    hide_index=True,
)

# ---------------------------------------------------------------------------
# Destockage flag disagreements
# ---------------------------------------------------------------------------
# A sell-in row asserts whether it came through the destockage channel; the SD
# dimension carries its own destocked flag. Where they disagree, one of them is
# wrong and the promo attainment built on top is measured against the wrong
# base -- which is why this sits on the page rather than only in an audit log.
with st.spinner("Checking destockage flags…"):
    mismatches = get_destocked_flag_mismatches(period, subregions=subregions)

if not mismatches.empty:
    st.warning(
        f"{fmt_number(len(mismatches))} sell-in row(s) disagree with the SD "
        f"destocked flag."
    )
    with st.expander("Review flag mismatches"):
        st.dataframe(mismatches, use_container_width=True, hide_index=True)