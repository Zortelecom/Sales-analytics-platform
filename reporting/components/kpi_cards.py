"""
reporting/components/kpi_cards.py
Reusable KPI card components.

Fixes in this revision
----------------------
1. COLUMN COUNT REGRESSION (mine). The previous revision always created
   `per_row` columns so multi-row layouts stayed aligned -- but that squeezed
   a single-card row into one fifth of the width, which is why the Innovation
   Revenue card wrapped to "INNOVAT / ION / REVENU / E" and "22.4 / M / FCF /
   A". Now: if every card fits on one row, use exactly that many columns;
   only pad when there is genuinely more than one row to keep aligned.

2. Rendering moved to `html_block()` (st.html), so indentation inside these
   templates can never be reinterpreted as a Markdown code block. That was
   the cause of the raw `<div style="color:#9CA3AF; ...` text in the
   Achievement card.

3. `render_page_header` renders at most once per script run per title. Two
   identical headers stacked on Executive Overview means something is calling
   it twice; this suppresses the duplicate. To find the real caller:
       Select-String -Path reporting\\app_pages\\*.py -Pattern render_page_header
"""
from __future__ import annotations

import html

import streamlit as st

from reporting.config import COLORS
from reporting.utils.formatters import (
    achievement_color,
    achievement_emoji,
    fmt_currency,
    fmt_delta,
    fmt_pct,
)
from reporting.utils.render import claim_once, html_block, keyed_container


def _card_html(
    title: str,
    value: str,
    subtitle: str = "",
    delta: str = "",
    delta_positive: bool = True,
    accent_color: str = COLORS["accent"],
    icon: str = "",
) -> str:
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

    return (
        f'<div style="background:{COLORS["bg_card"]}; border:1px solid {COLORS["border"]}; '
        f'border-top:3px solid {accent_color}; border-radius:8px; padding:1.1rem 1.2rem; '
        f'height:100%; box-sizing:border-box;">'
        f'<div style="color:{COLORS["text_secondary"]}; font-size:0.72rem; text-transform:uppercase; '
        f'letter-spacing:0.08em; margin-bottom:0.4rem;">{(icon + " ") if icon else ""}{title_safe}</div>'
        f'<div style="color:{COLORS["text_primary"]}; font-size:1.55rem; font-weight:700; '
        f'line-height:1.15; letter-spacing:-0.01em; overflow-wrap:anywhere;">{value_safe}</div>'
        f"{delta_html}{subtitle_html}"
        f"</div>"
    )


def kpi_card(title: str, value: str, subtitle: str = "",
             delta: str = "", delta_positive: bool = True,
             accent_color: str = COLORS["accent"], icon: str = "") -> None:
    html_block(_card_html(title, value, subtitle, delta, delta_positive, accent_color, icon))


def achievement_card(title: str, revenue: float, target: float,
                     prior_revenue: float | None = None) -> None:
    """Card for achievement % with colour coding."""
    pct = (revenue / target * 100) if target else None
    delta_str = ""
    delta_pos = True
    if prior_revenue is not None and prior_revenue > 0:
        yoy = (revenue - prior_revenue) / prior_revenue * 100
        delta_str = fmt_delta(yoy, is_pct=True) + " vs PY"
        delta_pos = yoy >= 0

    html_block(_card_html(
        title=title,
        value=f"{achievement_emoji(pct)} {fmt_pct(pct)}",
        subtitle=f"{fmt_currency(revenue, short=True)} / {fmt_currency(target, short=True)}",
        delta=delta_str,
        delta_positive=delta_pos,
        accent_color=achievement_color(pct),
        icon="🎯",
    ))


# Maximum cards per row at layout="wide". Rows with fewer cards than this are
# NOT padded out -- see the note in the module docstring.
CARDS_PER_ROW = 5


def render_kpi_row(kpis: list[dict], per_row: int = CARDS_PER_ROW) -> None:
    """Render KPI cards in rows of at most `per_row`.

    Single row  -> exactly len(kpis) columns, so one card spans the full width
                   and three cards each take a third.
    Multi row   -> per_row columns throughout, last row padded with empties so
                   card widths stay consistent between rows.
    """
    if not kpis:
        return

    if len(kpis) <= per_row:
        cols = st.columns(len(kpis))
        for col, kpi in zip(cols, kpis):
            with col:
                _render_one(kpi)
        return

    for start in range(0, len(kpis), per_row):
        chunk = kpis[start:start + per_row]
        cols = st.columns(per_row)
        for col, kpi in zip(cols, chunk):
            with col:
                _render_one(kpi)


def _render_one(kpi: dict) -> None:
    kpi_card(
        title=kpi.get("title", ""),
        value=kpi.get("value", "—"),
        subtitle=kpi.get("subtitle", ""),
        delta=kpi.get("delta", ""),
        delta_positive=kpi.get("delta_positive", True),
        accent_color=kpi.get("accent_color", COLORS["accent"]),
        icon=kpi.get("icon", ""),
    )


def render_section_header(title: str, subtitle: str = "") -> None:
    sub_html = (
        f'<p style="color:{COLORS["text_secondary"]}; font-size:0.85rem; margin:0;">{html.escape(subtitle)}</p>'
        if subtitle else ""
    )
    html_block(
        f'<div style="margin:1.5rem 0 0.8rem 0; border-left:3px solid {COLORS["accent"]}; padding-left:0.75rem;">'
        f'<h3 style="color:{COLORS["text_primary"]}; font-size:1.05rem; font-weight:600; margin:0;">'
        f"{html.escape(title)}</h3>{sub_html}</div>"
    )


def render_page_header(title: str, period_label: str, meeting_type: str) -> None:
    """Standardised page header.

    Two independent protections against the header rendering twice, because
    the two possible causes need different fixes:

      * `claim_once` stops a second *Python* call in the same script run.
      * `keyed_container` pins the block's element identity so Streamlit
        replaces it on rerun instead of appending a second copy, which is what
        happens when the element tree shifts between runs.
    """
    if not claim_once(f"page_header::{title}"):
        return

    badge = (
        f'<div style="background:{COLORS["bg_card"]}; border:1px solid {COLORS["border"]}; '
        f'border-radius:6px; padding:0.4rem 0.9rem; font-size:0.78rem; white-space:nowrap; '
        f'color:{COLORS["text_secondary"]};">📅 {html.escape(meeting_type.upper())} MEETING</div>'
        if meeting_type else ""
    )
    subtitle = html.escape(period_label)
    if meeting_type:
        subtitle += f" &nbsp;·&nbsp; {html.escape(meeting_type)} Report"

    slug = "".join(c if c.isalnum() else "_" for c in title.lower())
    with keyed_container(f"page_header_{slug}"):
        html_block(
            f'<div style="display:flex; align-items:center; justify-content:space-between; '
            f'margin-bottom:1.2rem; border-bottom:1px solid {COLORS["border"]}; '
            f'padding-bottom:0.8rem;">'
            f'<div><h1 style="margin:0; font-size:1.6rem; font-weight:700; '
            f'color:{COLORS["text_primary"]};">{html.escape(title)}</h1>'
            f'<p style="margin:0; color:{COLORS["text_secondary"]}; font-size:0.85rem;">'
            f"{subtitle}</p></div>{badge}</div>"
        )