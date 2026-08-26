"""
reporting/utils/filters.py
Shared sidebar filter builder -- returns a FilterState dict used by all pages.

WHAT CHANGED
------------
1. YEAR BASIS. The Period block now offers Calendar or Fiscal. The company's
   fiscal year opens in October and is named for the year it ends in:

       FY2026 = 2025-10-01 .. 2026-09-30

   The basis is carried in `f["period"]` -- a Period object (see
   reporting/utils/period.py) that knows how to express itself as SQL. Pages
   pass that object to the query builders instead of a bare year, because
   "FY2026" is not a value the `year` column can be compared against.

2. THE YEAR AND MONTH LISTS COME FROM ONE QUERY. available_year_months()
   returns the (year, month) pairs that actually have sales; both bases are
   derived from it. Deriving the fiscal picker from available_years() alone is
   impossible -- FY2026 needs to know that October 2025 exists.

3. `f["subregions"]` is unchanged, but it now reaches the SQL. It was always
   returned here and never consumed by a query builder, which is why it
   filtered nothing for admin and RBM users. See queries.py.

⚠ BACKWARD COMPATIBILITY. `f["year"]` is still a calendar year, for any page
not yet migrated to `f["period"]`. Under a FISCAL basis that value is the
calendar year of the selected month (or the year the FY ends in when no month
is selected) -- close, but NOT the fiscal year. A page that still passes
`f["year"]` while the sidebar says FY2026 will report calendar numbers under a
fiscal heading. Migrate the page; do not "fix" this default.
"""
from __future__ import annotations

from datetime import date

import streamlit as st

from reporting.config import DEFAULT_MEETING_TYPE, MEETING_TYPES
from reporting.utils.db import (
    available_categories,
    available_channels,
    available_regions,
    available_subregions,
    available_year_months,
    data_freshness,
)
from reporting.utils.period import (
    FISCAL_MONTH_ORDER,
    FISCAL_QUARTER_MONTHS,
    Period,
    fiscal_year_of,
)

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December",
}

# Fiscal year starts in October: Q1 = Oct-Dec.
# Imported from period.py so the sidebar and the SQL cannot drift apart -- the
# name is kept because pages import it.
QUARTER_MONTHS = FISCAL_QUARTER_MONTHS

BASIS_CALENDAR = "Calendar year"
BASIS_FISCAL = "Fiscal year (Oct–Sep)"

_CAPTION = (
    '<p style="color:#6B7280; font-size:0.75rem; text-transform:uppercase; '
    'letter-spacing:0.05em;">{}</p>'
)


def _years_for_basis(pairs: list[tuple[int, int]], fiscal: bool) -> list[int]:
    """Years with data, newest first, on the requested basis."""
    if fiscal:
        return sorted({fiscal_year_of(y, m) for y, m in pairs}, reverse=True)
    return sorted({y for y, _ in pairs}, reverse=True)


def _months_for_year(pairs: list[tuple[int, int]], year: int,
                     fiscal: bool) -> list[int]:
    """Months with data inside the selected year, in reading order.

    Fiscal reading order is October first. A fiscal year drawn January-first
    puts its opening quarter at the far right of every chart.
    """
    if fiscal:
        months = {m for y, m in pairs if fiscal_year_of(y, m) == year}
        return [m for m in FISCAL_MONTH_ORDER if m in months]
    return sorted({m for y, m in pairs if y == year})


def _month_label(month: int, year: int, fiscal: bool, with_year: bool) -> str:
    """Under a fiscal basis the calendar year is ALWAYS shown.

    "October" inside FY2026 means October 2025, and a reader taking a figure
    for the wrong year is exactly the failure this platform exists to prevent.
    The calendar year is spelled out rather than left to be inferred.
    """
    if fiscal:
        cal_year = year - 1 if month >= 10 else year
        return f"{MONTH_NAMES[month]} {cal_year}"
    return f"{MONTH_NAMES[month]} {year}" if with_year else MONTH_NAMES[month]


def render_sidebar_filters(show_month: bool = True,
                           show_region: bool = True,
                           show_subregion: bool = True,
                           show_channel: bool = True,
                           show_category: bool = False,
                           show_basis: bool = True) -> dict:
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

        pairs = available_year_months()
        if not pairs:
            st.warning("No data visible for your account, or the pipeline has not run yet.")
            st.stop()

        if show_basis:
            basis = st.radio(
                "Year Basis",
                [BASIS_CALENDAR, BASIS_FISCAL],
                index=0,
                horizontal=False,
                key="filter_basis",
                help="The fiscal year runs October to September and is named "
                     "for the year it ends in: FY2026 = Oct 2025 – Sep 2026.",
            )
        else:
            basis = BASIS_CALENDAR
        fiscal = basis == BASIS_FISCAL

        years = _years_for_basis(pairs, fiscal)
        if not years:
            st.warning("No periods with data.")
            st.stop()

        today = date.today()
        current = fiscal_year_of(today.year, today.month) if fiscal else today.year
        default_year_idx = years.index(current) if current in years else 0
        # A separate widget key per basis. Reusing one key would carry the
        # selected INDEX across a basis switch, so flipping to Fiscal while
        # 2024 was selected lands on a fiscal year nobody chose.
        year = st.selectbox(
            "Fiscal Year" if fiscal else "Year",
            years,
            index=default_year_idx,
            format_func=(lambda y: f"FY{y}") if fiscal else str,
            key="filter_fiscal_year" if fiscal else "filter_year",
        )

        selected_month = None
        selected_quarter = None
        selected_months: list[int] = []
        months = _months_for_year(pairs, year, fiscal)

        if show_month and meeting_type in ("Weekly", "Monthly"):
            if not months:
                st.warning(f"No months with data in {'FY' if fiscal else ''}{year}.")
                st.stop()
            with_year = meeting_type == "Weekly"
            labels = [_month_label(m, year, fiscal, with_year) for m in months]
            default_m_idx = months.index(today.month) if today.month in months else 0
            widget_key = "filter_month_weekly" if meeting_type == "Weekly" else "filter_month"
            selected_label = st.selectbox("Month", labels, index=default_m_idx, key=widget_key)
            selected_month = months[labels.index(selected_label)]
            selected_months = [selected_month]

        elif meeting_type == "Quarterly":
            selected_quarter = st.selectbox(
                "Quarter", [1, 2, 3, 4], format_func=lambda q: f"Q{q}", key="filter_quarter"
            )
            selected_months = list(QUARTER_MONTHS[selected_quarter])

        else:  # Annual, or a page that hides the month picker
            selected_months = list(range(1, 13))

        # months=None for a whole year rather than the twelve month numbers:
        # the predicate stays one equality per calendar year, and it reads
        # correctly in the query log.
        whole_year = (meeting_type not in ("Weekly", "Monthly", "Quarterly")
                      or not selected_months
                      or selected_month is None and selected_quarter is None)
        period = Period(
            year=year,
            months=None if whole_year else tuple(selected_months),
            fiscal=fiscal,
        )

        st.markdown("---")

        selected_regions: list[str] = []
        selected_subregions: list[str] = []

        if show_region or show_subregion:
            st.markdown(_CAPTION.format("Scope"), unsafe_allow_html=True)

        if show_region:
            selected_regions = st.multiselect(
                "Region", available_regions(), default=[],
                key="filter_regions", placeholder="All regions",
            )

        if show_subregion:
            # Derived from the selected regions rather than re-fetched
            # unfiltered.
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

        st.markdown("---")
        fresh = data_freshness()
        fresh_text = f"Last sale: {fresh}" if fresh else "No data"
        st.markdown(
            f'<p style="color:#374151; font-size:0.65rem; text-align:center;">'
            f"{fresh_text}<br>Data refreshes every 5 min</p>",
            unsafe_allow_html=True,
        )

    # `year` is the CALENDAR year for unmigrated pages -- see the module
    # docstring. Migrated pages read `period` and ignore this.
    legacy_year = year
    if fiscal:
        legacy_year = (year - 1) if (selected_month or 0) >= 10 else year

    return {
        "period": period,                  # ← what every migrated page uses
        "fiscal": fiscal,
        "fiscal_year": year if fiscal else None,
        "year": legacy_year,               # calendar year, legacy
        "month": selected_month,           # None for quarterly/annual
        "quarter": selected_quarter,
        "months": selected_months,
        "regions": selected_regions or None,
        "subregions": selected_subregions or None,
        "channels": selected_channels or None,
        "categories": selected_categories or None,
        "meeting_type": meeting_type,
    }


def period_label(f: dict) -> str:
    """The heading every page prints, built once so they cannot disagree.

    Under a fiscal basis the month carries its calendar year, because "October
    FY2026" is October 2025 and a reader should never have to do that
    arithmetic to know which month a number belongs to.
    """
    period, fiscal = f["period"], f["fiscal"]
    if f["month"]:
        label = _month_label(f["month"], period.year, fiscal, with_year=True)
        return f"{label} · {period.label}" if fiscal else label
    if f["quarter"]:
        return f"Q{f['quarter']} {period.label}"
    return f"Full Year {period.label}" + (" (Oct–Sep)" if fiscal else "")