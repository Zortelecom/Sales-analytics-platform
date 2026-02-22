"""
reporting/components/charts.py
All Plotly chart builders with consistent dark theme.
"""
from __future__ import annotations
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from reporting.config import COLORS
from reporting.utils.formatters import fmt_currency, fmt_pct, month_name


# ---------------------------------------------------------------------------
# Base layout — applied to all charts
# ---------------------------------------------------------------------------
BASE_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=COLORS["text_secondary"], size=11),
    margin=dict(l=8, r=8, t=32, b=8),
    legend=dict(
        bgcolor="rgba(0,0,0,0)",
        font=dict(color=COLORS["text_secondary"], size=10),
        orientation="h",
        yanchor="bottom", y=1.02,
        xanchor="left", x=0,
    ),
    xaxis=dict(
        gridcolor=COLORS["border"],
        linecolor=COLORS["border"],
        tickfont=dict(color=COLORS["text_secondary"], size=10),
    ),
    yaxis=dict(
        gridcolor=COLORS["border"],
        linecolor=COLORS["border"],
        tickfont=dict(color=COLORS["text_secondary"], size=10),
    ),
)


def _apply_base(fig: go.Figure, title: str = "", height: int = 320) -> go.Figure:
    layout = {**BASE_LAYOUT, "height": height}
    if title:
        layout["title"] = dict(text=title, font=dict(color=COLORS["text_primary"], size=13),
                                x=0, xanchor="left")
    fig.update_layout(**layout)
    return fig


# ---------------------------------------------------------------------------
# Revenue vs Target — bar + line combo
# ---------------------------------------------------------------------------
def revenue_vs_target_chart(df: pd.DataFrame,
                             x_col: str,
                             revenue_col: str = "revenue",
                             target_col: str = "target",
                             x_label_fn=None,
                             title: str = "Revenue vs Target",
                             height: int = 320) -> None:
    if df.empty:
        st.info("No data for this period."); return

    labels = df[x_col].apply(x_label_fn).tolist() if x_label_fn else df[x_col].tolist()
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=df[revenue_col],
        name="Revenue",
        marker_color=COLORS["accent"],
        marker_line_width=0,
        hovertemplate="%{x}<br>Revenue: %{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Scatter(
        x=labels, y=df[target_col],
        mode="lines+markers",
        name="Target",
        line=dict(color=COLORS["danger"], width=2, dash="dot"),
        marker=dict(size=5, color=COLORS["danger"]),
        hovertemplate="%{x}<br>Target: %{y:,.0f}<extra></extra>",
    ))
    _apply_base(fig, title, height)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Achievement gauge
# ---------------------------------------------------------------------------
def achievement_gauge(pct: float | None, title: str = "Achievement", height: int = 220) -> None:
    val = pct or 0
    color = (COLORS["success"] if val >= 100 else
             COLORS["accent"] if val >= 85 else
             COLORS["warning"] if val >= 70 else COLORS["danger"])

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=val,
        number=dict(suffix="%", font=dict(color=COLORS["text_primary"], size=28)),
        title=dict(text=title, font=dict(color=COLORS["text_secondary"], size=12)),
        gauge=dict(
            axis=dict(range=[0, 130], tickwidth=1, tickcolor=COLORS["border"],
                      tickfont=dict(color=COLORS["text_secondary"], size=9)),
            bar=dict(color=color, thickness=0.65),
            bgcolor=COLORS["bg_card_alt"],
            borderwidth=0,
            steps=[
                dict(range=[0, 70],  color="#1a1a2e"),
                dict(range=[70, 85], color="#1a2020"),
                dict(range=[85, 100],color="#1a2515"),
                dict(range=[100, 130], color="#0a2010"),
            ],
            threshold=dict(
                line=dict(color=COLORS["text_primary"], width=2),
                thickness=0.75, value=100,
            ),
        ),
    ))
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        height=height,
        margin=dict(l=16, r=16, t=32, b=8),
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Horizontal bar — rankings
# ---------------------------------------------------------------------------
def horizontal_bar_chart(df: pd.DataFrame,
                          y_col: str,
                          x_col: str = "revenue",
                          color_col: str | None = None,
                          title: str = "",
                          height: int = 320,
                          max_rows: int = 15) -> None:
    if df.empty:
        st.info("No data."); return
    df = df.copy().tail(max_rows)  # plotly horizontal bars: last = top

    bar_colors = [COLORS["accent"]] * len(df)
    if color_col and color_col in df.columns:
        bar_colors = [
            achievement_color_from_pct(p) for p in df[color_col]
        ]

    fig = go.Figure(go.Bar(
        x=df[x_col],
        y=df[y_col],
        orientation="h",
        marker_color=bar_colors,
        marker_line_width=0,
        hovertemplate=f"%{{y}}<br>{x_col.replace('_',' ').title()}: %{{x:,.0f}}<extra></extra>",
        text=[fmt_currency(v, short=True) for v in df[x_col]],
        textposition="outside",
        textfont=dict(color=COLORS["text_secondary"], size=10),
    ))
    _apply_base(fig, title, height)
    fig.update_layout(yaxis=dict(tickfont=dict(size=10)))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def achievement_color_from_pct(pct) -> str:
    from reporting.utils.formatters import achievement_color
    return achievement_color(pct)


# ---------------------------------------------------------------------------
# Line trend
# ---------------------------------------------------------------------------
def trend_line_chart(df: pd.DataFrame,
                     x_col: str,
                     y_cols: list[str],
                     names: list[str] | None = None,
                     colors: list[str] | None = None,
                     title: str = "",
                     height: int = 300,
                     x_label_fn=None) -> None:
    if df.empty:
        st.info("No data."); return
    names = names or y_cols
    colors = colors or COLORS["chart"]
    labels = df[x_col].apply(x_label_fn).tolist() if x_label_fn else df[x_col].tolist()
    fig = go.Figure()
    for i, (col, name) in enumerate(zip(y_cols, names)):
        fig.add_trace(go.Scatter(
            x=labels,
            y=df[col],
            name=name,
            mode="lines+markers",
            line=dict(color=colors[i % len(colors)], width=2),
            marker=dict(size=5),
            fill="tozeroy" if i == 0 else "none",
            fillcolor=f"rgba({_hex_to_rgb(colors[i % len(colors)])}, 0.08)",
            hovertemplate=f"{name}: %{{y:,.0f}}<extra></extra>",
        ))
    _apply_base(fig, title, height)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _hex_to_rgb(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    return ",".join(str(int(h[i:i+2], 16)) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# Donut / Pie for category breakdown
# ---------------------------------------------------------------------------
def donut_chart(df: pd.DataFrame,
                label_col: str,
                value_col: str = "revenue",
                title: str = "",
                height: int = 300) -> None:
    if df.empty:
        st.info("No data."); return
    fig = go.Figure(go.Pie(
        labels=df[label_col],
        values=df[value_col],
        hole=0.55,
        marker=dict(colors=COLORS["chart"]),
        textfont=dict(color=COLORS["text_secondary"], size=10),
        hovertemplate="%{label}<br>%{value:,.0f}<br>%{percent}<extra></extra>",
    ))
    _apply_base(fig, title, height)
    fig.update_layout(
        legend=dict(orientation="v", x=1, y=0.5,
                    font=dict(color=COLORS["text_secondary"], size=10)),
        showlegend=True,
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Waterfall / WoW chart
# ---------------------------------------------------------------------------
def waterfall_chart(df: pd.DataFrame,
                    x_col: str,
                    value_col: str = "revenue",
                    change_col: str = "wow_pct",
                    title: str = "Week-over-Week",
                    height: int = 300) -> None:
    if df.empty:
        st.info("No data."); return

    labels = [f"W{int(r[x_col])}" for _, r in df.iterrows()]
    values = df[value_col].tolist()
    measures = ["absolute"] * len(values)

    fig = go.Figure(go.Waterfall(
        name="Revenue",
        orientation="v",
        measure=measures,
        x=labels,
        y=values,
        textposition="outside",
        text=[fmt_currency(v, short=True) for v in values],
        connector=dict(line=dict(color=COLORS["border"])),
        increasing=dict(marker=dict(color=COLORS["success"])),
        decreasing=dict(marker=dict(color=COLORS["danger"])),
        totals=dict(marker=dict(color=COLORS["accent"])),
    ))
    _apply_base(fig, title, height)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Heatmap (seasonality)
# ---------------------------------------------------------------------------
def heatmap_chart(df: pd.DataFrame,
                  x_col: str, y_col: str, value_col: str = "revenue",
                  title: str = "Seasonality Heatmap",
                  height: int = 280) -> None:
    if df.empty:
        st.info("No data."); return

    pivot = df.pivot_table(index=y_col, columns=x_col, values=value_col, aggfunc="sum").fillna(0)

    fig = go.Figure(go.Heatmap(
        z=pivot.values,
        x=[month_name(int(c)) for c in pivot.columns],
        y=pivot.index.tolist(),
        colorscale=[[0, "#0A0E1A"], [0.5, "#92400E"], [1, COLORS["accent"]]],
        hovertemplate="Month: %{x}<br>%{y}<br>Revenue: %{z:,.0f}<extra></extra>",
        showscale=True,
        colorbar=dict(tickfont=dict(color=COLORS["text_secondary"], size=9)),
    ))
    _apply_base(fig, title, height)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


# ---------------------------------------------------------------------------
# Scatter — salesperson performance bubble
# ---------------------------------------------------------------------------
def performance_scatter(df: pd.DataFrame,
                         x_col: str = "target",
                         y_col: str = "revenue",
                         size_col: str = "active_clients",
                         label_col: str = "salesperson_name",
                         color_col: str = "achievement_pct",
                         title: str = "Revenue vs Target",
                         height: int = 380) -> None:
    if df.empty:
        st.info("No data."); return

    fig = px.scatter(
        df,
        x=x_col, y=y_col,
        size=size_col if size_col in df.columns else None,
        color=color_col if color_col in df.columns else None,
        text=label_col if label_col in df.columns else None,
        color_continuous_scale=[
            [0.0, COLORS["danger"]], [0.7, COLORS["warning"]],
            [0.85, COLORS["accent"]], [1.0, COLORS["success"]],
        ],
        range_color=[0, 130],
    )
    # Add diagonal (target = revenue line)
    max_val = max(df[x_col].max(), df[y_col].max()) * 1.05
    fig.add_shape(
        type="line", x0=0, y0=0, x1=max_val, y1=max_val,
        line=dict(color=COLORS["border"], width=1, dash="dot"),
    )
    fig.update_traces(
        textposition="top center",
        textfont=dict(color=COLORS["text_secondary"], size=8),
        marker=dict(line=dict(width=0)),
    )
    _apply_base(fig, title, height)
    fig.update_coloraxes(colorbar=dict(tickfont=dict(color=COLORS["text_secondary"], size=9),
                                        title=dict(text="Ach%", font=dict(size=10))))
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})
