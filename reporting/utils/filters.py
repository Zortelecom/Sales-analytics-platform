"""
reporting/utils/filters.py
Shared sidebar filter builder — returns a FilterState dict used by all pages.
"""
from __future__ import annotations
import streamlit as st
from datetime import date
from reporting.utils.db import available_years, available_months, available_regions, available_channels, available_categories
from reporting.config import MEETING_TYPES, COLORS
import calendar


MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

QUARTER_MONTHS = {1: [1,2,3], 2: [4,5,6], 3: [7,8,9], 4: [10,11,12]}


def render_sidebar_filters(show_month: bool = True,
                           show_region: bool = True,
                           show_channel: bool = True,
                           show_category: bool = False) -> dict:
    """
    Render the sidebar and return a FilterState dictionary.
    All pages should call this and use the returned dict.
    """
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
            f'<div style="text-align:center; padding: 1rem 0;">'
            f'<span style="font-size:1.8rem;">📊</span><br>'
            f'<span style="color:#F59E0B; font-weight:700; font-size:1rem; letter-spacing:0.05em;">SALES ANALYTICS</span>'
            f'</div>',
            unsafe_allow_html=True,
        )

        st.markdown("---")

        # Meeting type selector
        meeting_type = st.radio(
            "Meeting Mode",
            MEETING_TYPES,
            horizontal=False,
            key="meeting_type",
        )

        st.markdown("---")
        st.markdown('<p style="color:#6B7280; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;">Period</p>', unsafe_allow_html=True)

        # Year
        years = available_years()
        if not years:
            st.warning("No data found. Run the pipeline first.")
            st.stop()

        current_year = date.today().year
        default_year_idx = 0
        if current_year in years:
            default_year_idx = years.index(current_year)

        year = st.selectbox("Year", years, index=default_year_idx, key="filter_year")

        # Month (contextual)
        selected_month = None
        selected_quarter = None
        selected_months: list[int] = []

        if meeting_type == "Weekly":
            months = available_months(year)
            month_labels = [f"{MONTH_NAMES[m]} {year}" for m in months]
            current_month = date.today().month
            default_m_idx = 0
            if current_month in months:
                default_m_idx = months.index(current_month)
            selected_label = st.selectbox("Month", month_labels, index=default_m_idx, key="filter_month_weekly")
            selected_month = months[month_labels.index(selected_label)]
            selected_months = [selected_month]

        elif meeting_type == "Monthly":
            months = available_months(year)
            month_labels = [MONTH_NAMES[m] for m in months]
            current_month = date.today().month
            default_m_idx = 0
            if current_month in months:
                default_m_idx = months.index(current_month)
            selected_label = st.selectbox("Month", month_labels, index=default_m_idx, key="filter_month")
            selected_month = months[month_labels.index(selected_label)]
            selected_months = [selected_month]

        elif meeting_type == "Quarterly":
            quarter = st.selectbox("Quarter", [1, 2, 3, 4],
                                   format_func=lambda q: f"Q{q}",
                                   key="filter_quarter")
            selected_quarter = quarter
            selected_months = QUARTER_MONTHS[quarter]

        else:  # Annual
            selected_months = list(range(1, 13))

        st.markdown("---")

        # Region filter
        selected_regions = []
        if show_region:
            st.markdown('<p style="color:#6B7280; font-size:0.75rem; text-transform:uppercase; letter-spacing:0.05em;">Scope</p>', unsafe_allow_html=True)
            regions = available_regions()
            selected_regions = st.multiselect(
                "Region", regions, default=[], key="filter_regions",
                placeholder="All regions"
            )

        # Channel filter
        selected_channels = []
        if show_channel:
            channels = available_channels()
            selected_channels = st.multiselect(
                "Sales Channel", channels, default=[], key="filter_channels",
                placeholder="All channels"
            )

        # Category filter
        selected_categories = []
        if show_category:
            categories = available_categories()
            selected_categories = st.multiselect(
                "Product Category", categories, default=[], key="filter_categories",
                placeholder="All categories"
            )

        st.markdown("---")
        st.markdown(
            f'<p style="color:#374151; font-size:0.65rem; text-align:center;">'
            f'Data refreshes every 5 min</p>',
            unsafe_allow_html=True,
        )

    # Determine effective month for single-month queries
    effective_month = selected_month  # None for quarterly/annual

    return {
        "year": year,
        "month": effective_month,
        "quarter": selected_quarter,
        "months": selected_months,
        "regions": selected_regions or None,
        "channels": selected_channels or None,
        "categories": selected_categories or None,
        "meeting_type": meeting_type,
    }
