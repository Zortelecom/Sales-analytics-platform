"""
reporting/utils/filters.py
Shared sidebar filter builder -- returns a FilterState dict used by all pages.

Fixes in this revision
----------------------
1. The subregion block called `available_subregions()` with no argument,
   throwing away the region-aware list computed immediately above it. Picking
   "Littoral" still offered every subregion in the country. Now uses the
   narrowed list.
2. Selecting a region no longer leaves a stale subregion selected -- stale
   values are dropped so the returned filter can't reference a subregion
   outside the chosen regions.
3. The "SCOPE" caption was rendered twice (once for region, once for
   subregion). Rendered once now.
4. `data_freshness` import moved to module level (it was inside the function).
5. Option lists come from db.py, which applies row-level security -- a scoped
   user is never offered a region they may not read.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from reporting.config import DEFAULT_MEETING_TYPE, MEETING_TYPES
from reporting.utils.db import (
    available_categories,
    available_channels,
    available_months,
    available_regions,
    available_subregions,
    available_years,
    data_freshness,
)

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Fiscal year starts in October: Q1 = Oct-Dec.
QUARTER_MONTHS = {1: [10, 11, 12], 2: [1, 2, 3], 3: [4, 5, 6], 4: [7, 8, 9]}

_CAPTION = (
    '<p style="color:#6B7280; font-size:0.75rem; text-transform:uppercase; '
    'letter-spacing:0.05em;">{}</p>'
)


def render_sidebar_filters(show_month: bool = True,
                           show_region: bool = True,
                           show_subregion: bool = True,
                           show_channel: bool = True,
                           show_category: bool = False) -> dict:
    """Render the sidebar and return a FilterState dictionary."""
    with st.sidebar:
        st.markdown(
            """
            <style>
            [data-testid="stSidebar"] { background: #0D1117; border-right: 1px solid #1F2937; }
            [data-testid="stSidebar"] .stSelectbox label,
            [data-testid="stSidebar"] .stMultiSelect label,
            [data-testid="stSidebar"] .stRadio label { color: #9CA3AF !important; font-size: 0.78rem; text-transform: uppercase; letter-spacing: 0.05em; }
            </style>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            '<div style="text-align:center; padding: 0.5rem 0;">'
            '<span style="font-size:1.8rem;">📊</span><br>'
            '<span style="color:#F59E0B; font-weight:700; font-size:1rem; letter-spacing:0.05em;">SALES ANALYTICS</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        st.markdown("---")

        meeting_type = st.radio(
            "Meeting Mode",
            MEETING_TYPES,
            index=MEETING_TYPES.index(DEFAULT_MEETING_TYPE),
            horizontal=False,
            key="meeting_type",
        )

        st.markdown("---")
        st.markdown(_CAPTION.format("Period"), unsafe_allow_html=True)

        years = available_years()
        if not years:
            st.warning("No data visible for your account, or the pipeline has not run yet.")
            st.stop()

        current_year = date.today().year
        default_year_idx = years.index(current_year) if current_year in years else 0
        year = st.selectbox("Year", years, index=default_year_idx, key="filter_year")

        selected_month = None
        selected_quarter = None
        selected_months: list[int] = []
        months = available_months(year)

        if meeting_type in ("Weekly", "Monthly"):
            if not months:
                st.warning(f"No months with data in {year}.")
                st.stop()
            labels = ([f"{MONTH_NAMES[m]} {year}" for m in months]
                      if meeting_type == "Weekly" else [MONTH_NAMES[m] for m in months])
            current_month = date.today().month
            default_m_idx = months.index(current_month) if current_month in months else 0
            widget_key = "filter_month_weekly" if meeting_type == "Weekly" else "filter_month"
            selected_label = st.selectbox("Month", labels, index=default_m_idx, key=widget_key)
            selected_month = months[labels.index(selected_label)]
            selected_months = [selected_month]

        elif meeting_type == "Quarterly":
            selected_quarter = st.selectbox(
                "Quarter", [1, 2, 3, 4], format_func=lambda q: f"Q{q}", key="filter_quarter"
            )
            selected_months = QUARTER_MONTHS[selected_quarter]

        else:  # Annual
            selected_months = list(range(1, 13))

        st.markdown("---")

        scope_caption_rendered = False
        selected_regions: list[str] = []
        selected_subregions: list[str] = []

        if show_region or show_subregion:
            st.markdown(_CAPTION.format("Scope"), unsafe_allow_html=True)
            scope_caption_rendered = True

        if show_region:
            selected_regions = st.multiselect(
                "Region", available_regions(), default=[],
                key="filter_regions", placeholder="All regions",
            )

        if show_subregion:
            # FIX: this list is now derived from the selected regions instead
            # of being re-fetched unfiltered.
            subregions = available_subregions(selected_regions or None)
            # Drop any previously-selected subregion that the new region choice
            # excludes, so the returned filter can never be self-contradictory.
            previous = st.session_state.get("filter_subregions", [])
            if previous and any(s not in subregions for s in previous):
                st.session_state["filter_subregions"] = [s for s in previous if s in subregions]
            selected_subregions = st.multiselect(
                "Subregion", subregions, default=[],
                key="filter_subregions", placeholder="All subregions",
            )

        selected_channels: list[str] = []
        if show_channel:
            selected_channels = st.multiselect(
                "Sales Channel", available_channels(), default=[],
                key="filter_channels", placeholder="All channels",
            )

        selected_categories: list[str] = []
        if show_category:
            selected_categories = st.multiselect(
                "Product Category", available_categories(), default=[],
                key="filter_categories", placeholder="All categories",
            )

        _ = scope_caption_rendered  # kept for readability of the block above

        st.markdown("---")
        fresh = data_freshness()
        fresh_text = f"Last sale: {fresh}" if fresh else "No data"
        st.markdown(
            f'<p style="color:#374151; font-size:0.65rem; text-align:center;">'
            f"{fresh_text}<br>Data refreshes every 5 min</p>",
            unsafe_allow_html=True,
        )

    return {
        "year": year,
        "month": selected_month,           # None for quarterly/annual
        "quarter": selected_quarter,
        "months": selected_months,
        "regions": selected_regions or None,
        "subregions": selected_subregions or None,
        "channels": selected_channels or None,
        "categories": selected_categories or None,
        "meeting_type": meeting_type,
    }