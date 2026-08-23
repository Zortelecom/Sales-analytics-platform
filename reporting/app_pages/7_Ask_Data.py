# reporting/pages/7_Ask_Data.py
#
# Fixes applied (reporting-layer review):
#   - is_safe_sql() was imported-worthy but never called: the LLM's SQL ran
#     directly against the serving DB with no guard at all. It is now
#     checked before execution, and the query is wrapped with
#     enforce_row_limit() so the 500-row cap is guaranteed rather than
#     merely requested in the system prompt.
#   - Removed duplicate imports (streamlit, text_to_sql/interpret_result
#     were each imported twice).

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
import plotly.express as px

from reporting.utils.db import query as db_query
from reporting.utils.ask import text_to_sql, interpret_result, is_safe_sql, enforce_row_limit
from reporting.utils.schema_context import get_schema_context
from reporting.components.charts import trend_line_chart, horizontal_bar_chart
from reporting.config import COLORS

st.markdown(f'<h1 style="color:{COLORS["text_primary"]}">💬 Ask Your Data</h1>',
            unsafe_allow_html=True)
st.markdown(
    '<p style="color:#9CA3AF;">Type a question in plain English. '
    'Examples: <em>"Which salesperson had the highest revenue in Q2?"</em>, '
    '<em>"Show me monthly trend for innovation products in 2025."</em></p>',
    unsafe_allow_html=True,
)

question = st.text_input("Your question", placeholder="e.g. Top 5 regions by revenue this year")

if question:
    with st.spinner("Thinking…"):
        schema_ctx = get_schema_context()
        sql = text_to_sql(question, schema_ctx)

    with st.expander("Generated SQL", expanded=False):
        st.code(sql, language="sql")

    if not is_safe_sql(sql):
        st.error(
            "⚠️ The generated query didn't pass the safety check, so it wasn't run. "
            "Try rephrasing your question."
        )
        st.stop()

    safe_sql = enforce_row_limit(sql)

    try:
        df = db_query(safe_sql)
    except Exception as e:
        st.error(f"SQL execution failed: {e}")
        st.stop()

    if df.empty:
        st.info("The query returned no rows for this question.")
        st.stop()

    with st.spinner("Interpreting results…"):
        narrative, spec = interpret_result(question, sql, df)

    st.markdown(
        f'<div style="background:{COLORS["bg_card"]}; border-left:3px solid {COLORS["accent"]}; '
        f'padding:0.75rem 1rem; border-radius:4px; margin-bottom:1rem;">'
        f'<p style="color:{COLORS["text_primary"]}; margin:0;">{narrative}</p></div>',
        unsafe_allow_html=True,
    )

    chart_type = spec.get("chart", "table")
    x_col = spec.get("x")
    y_col = spec.get("y")

    if chart_type == "metric" and len(df) == 1 and len(df.columns) == 1:
        val = df.iloc[0, 0]
        st.metric(label=df.columns[0], value=val)

    elif chart_type == "bar" and x_col in df.columns and y_col in df.columns:
        fig = px.bar(df, x=x_col, y=y_col,
                     template="plotly_dark",
                     color_discrete_sequence=[COLORS["accent"]])
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    elif chart_type == "line" and x_col in df.columns and y_col in df.columns:
        fig = px.line(df, x=x_col, y=y_col,
                      template="plotly_dark",
                      color_discrete_sequence=[COLORS["accent"]])
        fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
        st.plotly_chart(fig, use_container_width=True)

    else:
        st.dataframe(df, use_container_width=True)

    st.download_button("⬇ Export CSV", df.to_csv(index=False),
                       file_name="query_result.csv", mime="text/csv")
