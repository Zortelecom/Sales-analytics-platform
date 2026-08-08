"""
reporting/components/ranking_table.py
Styled ranking and comparison tables.

Fixes in this revision
----------------------
1. StreamlitDuplicateElementKey. `_export_csv_button` used
   `key=f"dl_{file_name}"`, and `file_name` was built only from `name_col`.
   Salesforce Performance renders two `salesperson_name` tables (Team Ranking
   and the per-supervisor team table) and Product Performance renders two
   `product_category` tables, so the second call collided and crashed the
   page. Keys now go through `unique_key()`, which appends `__2`, `__3`...
   within a script run and stays stable across reruns.

2. `render_comparison_table` built `rows_html` inside an indented f-string and
   rendered it through `st.markdown(unsafe_allow_html=True)`, so Markdown's
   indented-code-block rule printed the `<tr>` source instead of rendering it.
   Both tables now go through `html_block()` (st.html), which skips the
   Markdown parser entirely.

3. The CSV export button now respects the signed-in user's `can_export` flag.
   Previously every role could export the full table regardless.
"""
from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from reporting.config import COLORS
from reporting.utils.formatters import (
    achievement_color,
    achievement_emoji,
    fmt_currency,
    fmt_pct,
)
from reporting.utils.render import html_block, unique_key


def _can_export() -> bool:
    """True unless the signed-in user is explicitly denied exports."""
    try:
        from reporting.auth.session import current_user
        user = current_user()
        return True if user is None else user.can_export
    except Exception:
        return True


def _achievement_badge(pct: float | None) -> str:
    color = achievement_color(pct)
    emoji = achievement_emoji(pct)
    text = fmt_pct(pct) if pct is not None else "—"
    return f'<span style="color:{color}; font-weight:600;">{emoji} {text}</span>'


def _export_csv_button(df: pd.DataFrame, file_name: str) -> None:
    """Inline CSV export button. Key is disambiguated per script run."""
    if not _can_export():
        return
    csv = df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇ Export CSV",
        data=csv,
        file_name=file_name,
        mime="text/csv",
        key=unique_key(f"dl_{file_name}"),
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
    """Render a styled ranking table with achievement colour coding."""
    if df.empty:
        st.info("No data available for this selection.")
        return

    df = df.copy().head(max_rows).reset_index(drop=True)

    if title:
        html_block(
            f'<h4 style="color:{COLORS["text_primary"]}; font-size:0.95rem; margin:0.5rem 0;">'
            f"{html.escape(title)}</h4>"
        )

    extra_cols = extra_cols or []
    rows_html = ""
    for i, row in df.iterrows():
        rank_html = (
            f'<td style="color:{COLORS["text_secondary"]}; text-align:center;">{i + 1}</td>'
            if rank_col else ""
        )
        rev_str = fmt_currency(row.get(revenue_col, 0), short=True)
        tgt_str = fmt_currency(row.get(target_col, 0), short=True)
        name_val = html.escape(str(row.get(name_col, "")))

        extras = "".join(
            f'<td style="color:{COLORS["text_secondary"]};">{html.escape(str(row.get(ec, "")))}</td>'
            for ec in extra_cols
        )

        rows_html += (
            f'<tr style="border-bottom:1px solid {COLORS["border"]};">'
            f"{rank_html}"
            f'<td style="color:{COLORS["text_primary"]}; font-weight:500;">{name_val}</td>'
            f'<td style="color:{COLORS["text_primary"]}; text-align:right; font-weight:600;">{rev_str}</td>'
            f'<td style="color:{COLORS["text_secondary"]}; text-align:right;">{tgt_str}</td>'
            f'<td style="text-align:center;">{_achievement_badge(row.get(achievement_col))}</td>'
            f"{extras}</tr>"
        )

    rank_header = '<th style="text-align:center; padding:0.5rem 0.25rem;">Rank</th>' if rank_col else ""
    extra_headers = "".join(
        f'<th style="text-align:left; padding:0.5rem 0.25rem;">{html.escape(ec.replace("_", " ").title())}</th>'
        for ec in extra_cols
    )

    html_block(
        '<div style="overflow-x:auto; margin-top:0.5rem;">'
        '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f'<thead><tr style="background:{COLORS["bg_card_alt"]}; color:{COLORS["text_secondary"]}; '
        'font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;">'
        f"{rank_header}"
        '<th style="text-align:left; padding:0.5rem 0.25rem;">Name</th>'
        '<th style="text-align:right; padding:0.5rem 0.25rem;">Revenue</th>'
        '<th style="text-align:right; padding:0.5rem 0.25rem;">Target</th>'
        '<th style="text-align:center; padding:0.5rem 0.25rem;">Achievement</th>'
        f"{extra_headers}</tr></thead>"
        f"<tbody>{rows_html}</tbody></table></div>"
    )

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
    """Side-by-side comparison table (CY vs PY, etc.)."""
    if df.empty:
        st.info("No data available.")
        return

    if title:
        html_block(
            f'<h4 style="color:{COLORS["text_primary"]}; font-size:0.95rem; margin:0.5rem 0;">'
            f"{html.escape(title)}</h4>"
        )

    rows_html = ""
    for _, row in df.iterrows():
        cy = row.get(value_col, 0)
        py = row.get(compare_col, 0) if compare_col else None
        pct = row.get(pct_change_col) if pct_change_col else None

        pct_color = COLORS["success"] if (pct or 0) >= 0 else COLORS["danger"]
        pct_html = (
            f'<td style="color:{pct_color}; text-align:center;">{fmt_pct(pct)}</td>'
            if pct is not None else ""
        )
        py_html = (
            f'<td style="color:{COLORS["text_secondary"]}; text-align:right;">'
            f"{fmt_currency(py, short=True)}</td>"
            if py is not None else ""
        )

        rows_html += (
            f'<tr style="border-bottom:1px solid {COLORS["border"]};">'
            f'<td style="color:{COLORS["text_primary"]};">{html.escape(str(row.get(group_col, "")))}</td>'
            f'<td style="color:{COLORS["text_primary"]}; text-align:right; font-weight:600;">'
            f"{fmt_currency(cy, short=True)}</td>"
            f"{py_html}{pct_html}</tr>"
        )

    py_header = '<th style="text-align:right; padding:0.5rem 0.25rem;">Prior Year</th>' if compare_col else ""
    pct_header = '<th style="text-align:center; padding:0.5rem 0.25rem;">Change</th>' if pct_change_col else ""

    html_block(
        '<div style="overflow-x:auto;">'
        '<table style="width:100%; border-collapse:collapse; font-size:0.85rem;">'
        f'<thead><tr style="background:{COLORS["bg_card_alt"]}; color:{COLORS["text_secondary"]}; '
        'font-size:0.72rem; text-transform:uppercase; letter-spacing:0.05em;">'
        f'<th style="text-align:left; padding:0.5rem 0.25rem;">'
        f'{html.escape(group_col.replace("_", " ").title())}</th>'
        '<th style="text-align:right; padding:0.5rem 0.25rem;">Current</th>'
        f"{py_header}{pct_header}</tr></thead>"
        f"<tbody>{rows_html}</tbody></table></div>"
    )

    if show_export:
        _export_csv_button(df, f"comparison_{group_col}.csv")