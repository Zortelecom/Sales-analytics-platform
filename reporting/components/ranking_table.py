"""
reporting/components/ranking_table.py
Styled ranking and comparison tables.

"""
from __future__ import annotations
import html
import pandas as pd
import streamlit as st
from reporting.utils.formatters import (
    fmt_currency, fmt_pct, fmt_number, achievement_color, achievement_emoji
)
from reporting.config import COLORS


def _achievement_badge(pct: float | None) -> str:
    color = achievement_color(pct)
    emoji = achievement_emoji(pct)
    text = fmt_pct(pct) if pct is not None else "—"
    return f'<span style="color:{color}; font-weight:600;">{emoji} {text}</span>'


def _export_csv_button(df: pd.DataFrame, file_name: str) -> None:
    """Inline CSV export button for tables (§4.2)."""
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇ Export CSV",
        data=csv,
        file_name=file_name,
        mime="text/csv",
        key=f"dl_{file_name}",
        on_click="ignore",
    )


def render_ranking_table(
    df: pd.DataFrame,
    name_col: str,
    revenue_col: str = "revenue",
    target_col: str = "target",
    achievement_col: str = "achievement_pct",
    extra_cols: list[str] | None = None,
    title: str = "",
    rank_col: bool = True,
    max_rows: int = 20,
    show_export: bool = True,
) -> None:
    """
    Render a styled ranking table with achievement color coding.
    Optionally shows a CSV download button (§4.2).
    """
    if df.empty:
        st.info("No data available for this selection.")
        return

    df = df.copy().head(max_rows).reset_index(drop=True)

    if title:
        st.markdown(
            f'<h4 style="color:{COLORS["text_primary"]}; font-size:0.95rem; margin:0.5rem 0;">{html.escape(title)}</h4>',
            unsafe_allow_html=True,
        )

    extra_cols = extra_cols or []
    rows_html = ""
    for i, row in df.iterrows():
        rank_html = f'<td style="color:{COLORS["text_secondary"]}; text-align:center;">{i+1}</td>' if rank_col else ""
        rev = row.get(revenue_col, 0)
        tgt = row.get(target_col, 0)
        ach = row.get(achievement_col)

        name_val = html.escape(str(row.get(name_col, '')))
        rev_str = fmt_currency(rev, short=True)
        tgt_str = fmt_currency(tgt, short=True)

        extras = ""
        for ec in extra_cols:
            val = row.get(ec, "")
            extras += f'<td style="color:{COLORS["text_secondary"]};">{html.escape(str(val))}</td>'

        rows_html += f"""<tr style="border-bottom: 1px solid {COLORS['border']};">
            {rank_html}
            <td style="color:{COLORS['text_primary']}; font-weight:500;">{name_val}</td>
            <td style="color:{COLORS['text_primary']}; text-align:right; font-weight:600;">{rev_str}</td>
            <td style="color:{COLORS['text_secondary']}; text-align:right;">{tgt_str}</td>
            <td style="text-align:center;">{_achievement_badge(ach)}</td>
            {extras}
        </tr>"""

    rank_header = "<th>Rank</th>" if rank_col else ""
    extra_headers = "".join(
        f'<th style="text-align:left;">{html.escape(ec.replace("_", " ").title())}</th>'
        for ec in extra_cols
    )

    html_content = f"""<div style="overflow-x:auto; margin-top:0.5rem;">
        <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
            <thead>
                <tr style="background:{COLORS['bg_card_alt']}; color:{COLORS['text_secondary']};
                        font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;">
                    {rank_header}
                    <th style="text-align:left; padding:0.5rem 0.25rem;">Name</th>
                    <th style="text-align:right; padding:0.5rem 0.25rem;">Revenue</th>
                    <th style="text-align:right; padding:0.5rem 0.25rem;">Target</th>
                    <th style="text-align:center; padding:0.5rem 0.25rem;">Achievement</th>
                    {extra_headers}
                </tr>
            </thead>
            <tbody>{rows_html}</tbody>
        </table>
        </div>"""

    try:
        st.html(html_content)
    except AttributeError:
        st.markdown(html_content, unsafe_allow_html=True)

    if show_export:
        _export_csv_button(df, f"ranking_{name_col}.csv")


def render_comparison_table(
    df: pd.DataFrame,
    group_col: str,
    value_col: str = "revenue",
    compare_col: str | None = None,
    pct_change_col: str | None = None,
    title: str = "",
    show_export: bool = True,
) -> None:
    """Simple side-by-side comparison table (CY vs PY, etc.)."""
    if df.empty:
        st.info("No data available.")
        return
    if title:
        st.markdown(
            f'<h4 style="color:{COLORS["text_primary"]}; font-size:0.95rem; margin:0.5rem 0;">{html.escape(title)}</h4>',
            unsafe_allow_html=True,
        )

    rows_html = ""
    for _, row in df.iterrows():
        cy = row.get(value_col, 0)
        py = row.get(compare_col, 0) if compare_col else None
        pct = row.get(pct_change_col) if pct_change_col else None
        pct_color = COLORS["success"] if (pct or 0) >= 0 else COLORS["danger"]
        pct_html = f'<td style="color:{pct_color}; text-align:center;">{fmt_pct(pct)}</td>' if pct is not None else ""
        py_html = f'<td style="color:{COLORS["text_secondary"]}; text-align:right;">{fmt_currency(py, short=True)}</td>' if py is not None else ""

        rows_html += f"""
        <tr style="border-bottom:1px solid {COLORS['border']};">
            <td style="color:{COLORS['text_primary']};">{html.escape(str(row.get(group_col, '')))}</td>
            <td style="color:{COLORS['text_primary']}; text-align:right; font-weight:600;">{fmt_currency(cy, short=True)}</td>
            {py_html}
            {pct_html}
        </tr>
        """

    py_header = '<th style="text-align:right;">Prior Year</th>' if compare_col else ""
    pct_header = '<th style="text-align:center;">Change</th>' if pct_change_col else ""

    # Variable renamed from `html` → `table_html` to avoid shadowing the html module
    table_html = f"""
    <div style="overflow-x:auto;">
    <table style="width:100%; border-collapse:collapse; font-size:0.85rem;">
        <thead>
            <tr style="background:{COLORS['bg_card_alt']}; color:{COLORS['text_secondary']};
                       font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;">
                <th style="text-align:left; padding:0.5rem 0.25rem;">{html.escape(group_col.replace('_',' ').title())}</th>
                <th style="text-align:right; padding:0.5rem 0.25rem;">Current</th>
                {py_header}
                {pct_header}
            </tr>
        </thead>
        <tbody>{rows_html}</tbody>
    </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)

    if show_export:
        _export_csv_button(df, f"comparison_{group_col}.csv")