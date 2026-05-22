# reporting/pages/7_Ask_Data.py

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
import reporting._bootstrap  # noqa: F401

import streamlit as st
from reporting.utils.db import query as db_query
from reporting.utils.ask import text_to_sql, interpret_result
from reporting.config import COLORS
from reporting.utils.schema_context import get_schema_context
from reporting.utils.ask import text_to_sql, interpret_result
from reporting.components.charts import trend_line_chart, horizontal_bar_chart
import plotly.express as px
import streamlit as st

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

    try:
        df = db_query(sql)
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