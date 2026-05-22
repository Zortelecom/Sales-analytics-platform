"""
reporting/components/kpi_cards.py
Reusable KPI card components rendered via st.markdown HTML.
"""
from __future__ import annotations
import html
import streamlit as st
from reporting.utils.formatters import (
    fmt_currency, fmt_pct, fmt_delta, achievement_color, achievement_emoji
)
from reporting.config import COLORS


def _card_html(
    title: str,
    value: str,
    subtitle: str = "",
    delta: str = "",
    delta_positive: bool = True,
    accent_color: str = COLORS["accent"],
    icon: str = "",
) -> str:
    # Escape HTML to prevent breaking the card structure
    subtitle_safe = html.escape(subtitle) if subtitle else ""
    delta_safe = html.escape(delta) if delta else ""
    title_safe = html.escape(title)
    value_safe = html.escape(value)
    
    delta_color = COLORS["success"] if delta_positive else COLORS["danger"]
    delta_html = (
        f'<div style="color:{delta_color}; font-size:0.78rem; margin-top:0.15rem;">{delta_safe}</div>'
        if delta else ""
    )
    subtitle_html = (
        f'<div style="color:{COLORS["text_secondary"]}; font-size:0.72rem; margin-top:0.2rem;">{subtitle_safe}</div>'
        if subtitle else ""
    )
    return f"""
    <div style="
        background: {COLORS['bg_card']};
        border: 1px solid {COLORS['border']};
        border-top: 3px solid {accent_color};
        border-radius: 8px;
        padding: 1.1rem 1.2rem;
        height: 100%;
        box-sizing: border-box;
    ">
        <div style="color:{COLORS['text_secondary']}; font-size:0.72rem;
                    text-transform:uppercase; letter-spacing:0.08em; margin-bottom:0.4rem;">
            {icon + ' ' if icon else ''}{title_safe}
        </div>
        <div style="color:{COLORS['text_primary']}; font-size:1.55rem;
                    font-weight:700; line-height:1.1; letter-spacing:-0.01em;">
            {value_safe}
        </div>
        {delta_html}
        {subtitle_html}
    </div>
    """


def kpi_card(title: str, value: str, subtitle: str = "",
             delta: str = "", delta_positive: bool = True,
             accent_color: str = COLORS["accent"], icon: str = "") -> None:
    st.markdown(
        _card_html(title, value, subtitle, delta, delta_positive, accent_color, icon),
        unsafe_allow_html=True,
    )


def achievement_card(title: str, revenue: float, target: float,
                     prior_revenue: float | None = None) -> None:
    """Special card for achievement % with color coding."""
    pct = (revenue / target * 100) if target else None
    color = achievement_color(pct)
    emoji = achievement_emoji(pct)
    delta_str = ""
    delta_pos = True
    if prior_revenue is not None and prior_revenue > 0:
        yoy = (revenue - prior_revenue) / prior_revenue * 100
        delta_str = fmt_delta(yoy, is_pct=True) + " vs PY"
        delta_pos = yoy >= 0

    st.markdown(
        _card_html(
            title=title,
            value=f"{emoji} {fmt_pct(pct)}",
            subtitle=f"{fmt_currency(revenue, short=True)} / {fmt_currency(target, short=True)}",
            delta=delta_str,
            delta_positive=delta_pos,
            accent_color=color,
            icon="🎯",
        ),
        unsafe_allow_html=True,
    )


def render_kpi_row(kpis: list[dict]) -> None:
    """
    Render a row of KPI cards.
    Each dict: {title, value, subtitle?, delta?, delta_positive?, accent_color?, icon?}
    Capped at 4 columns to prevent overflow on narrow viewports (§3.5).
    """
    n = min(len(kpis), 4)
    cols = st.columns(n)
    for col, kpi in zip(cols, kpis):
        with col:
            kpi_card(
                title=kpi.get("title", ""),
                value=kpi.get("value", "—"),
                subtitle=kpi.get("subtitle", ""),
                delta=kpi.get("delta", ""),
                delta_positive=kpi.get("delta_positive", True),
                accent_color=kpi.get("accent_color", COLORS["accent"]),
                icon=kpi.get("icon", ""),
            )
    # If >4 KPIs, wrap to a second row
    if len(kpis) > 4:
        render_kpi_row(kpis[4:])


def render_section_header(title: str, subtitle: str = "") -> None:
    sub_html = (
        f'<p style="color:{COLORS["text_secondary"]}; font-size:0.85rem; margin:0;">{subtitle}</p>'
        if subtitle else ""
    )
    st.markdown(
        f"""
        <div style="margin: 1.5rem 0 0.8rem 0; border-left: 3px solid {COLORS['accent']};
                    padding-left: 0.75rem;">
            <h3 style="color:{COLORS['text_primary']}; font-size:1.05rem;
                       font-weight:600; margin:0; letter-spacing:0.01em;">{title}</h3>
            {sub_html}
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_page_header(title: str, period_label: str, meeting_type: str) -> None:
    """Standardised page header used by all reporting pages (§5.2)."""
    st.markdown(
        f"""
        <div style="display:flex; align-items:center; justify-content:space-between;
                    margin-bottom:1.2rem; border-bottom:1px solid {COLORS['border']};
                    padding-bottom:0.8rem;">
            <div>
                <h1 style="margin:0; font-size:1.6rem; font-weight:700;
                           color:{COLORS['text_primary']};">{html.escape(title)}</h1>
                <p style="margin:0; color:{COLORS['text_secondary']}; font-size:0.85rem;">
                    {html.escape(period_label)} &nbsp;·&nbsp; {html.escape(meeting_type)} Report
                </p>
            </div>
            <div style="background:{COLORS['bg_card']}; border:1px solid {COLORS['border']};
                        border-radius:6px; padding:0.4rem 0.9rem; font-size:0.78rem;
                        color:{COLORS['text_secondary']};">
                📅 {html.escape(meeting_type.upper())} MEETING
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )