# reporting/utils/ask.py
from __future__ import annotations
import json
import anthropic
import pandas as pd
import streamlit as st
import re

_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env
_DISALLOWED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|COPY|EXPORT|ATTACH)\b",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """You are a SQL expert for a sales analytics DuckDB database.
Given the schema context and a user question, return ONLY a valid DuckDB SQL
SELECT query — no explanation, no markdown fences, no preamble.
Rules:
- Use only the views listed in the schema context.
- Do not use subqueries named 'query'.
- Limit results to 500 rows maximum.
- XAF amounts are integers; do not add decimal formatting inside SQL.
- For ranking questions, always include ORDER BY and LIMIT.
"""

def text_to_sql(question: str, schema_context: str) -> str:
    """Ask the LLM to convert a natural-language question to a SQL SELECT."""
    response = _client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"{schema_context}\n\nQuestion: {question}"}
        ],
    )
    sql = response.content[0].text.strip()
    # Strip accidental markdown fences
    if sql.startswith("```"):
        sql = "\n".join(sql.split("\n")[1:-1])
    return sql


NARRATE_SYSTEM = """You are a data analyst. Given a question, a SQL query,
and a JSON result table, write a concise 2-3 sentence answer in plain English.
Then output a JSON object on the last line:
{"chart": "bar"|"line"|"table"|"metric"|"none", "x": "<col>", "y": "<col>"}
Pick the most appropriate chart type. Use "metric" for single-value answers.
"""

def interpret_result(question: str, sql: str, df: pd.DataFrame) -> tuple[str, dict]:
    """Return (narrative_text, chart_spec)."""
    sample = df.head(20).to_json(orient="records")
    response = _client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        system=NARRATE_SYSTEM,
        messages=[{"role": "user", "content":
            f"Question: {question}\nSQL: {sql}\nResult (first 20 rows): {sample}"}],
    )
    raw = response.content[0].text.strip()
    # Split narrative from JSON spec on last line
    lines = raw.strip().split("\n")
    try:
        spec = json.loads(lines[-1])
        narrative = "\n".join(lines[:-1]).strip()
    except (json.JSONDecodeError, IndexError):
        spec = {"chart": "table", "x": None, "y": None}
        narrative = raw
    return narrative, spec

def is_safe_sql(sql: str) -> bool:
    return sql.strip().upper().startswith("SELECT") and not _DISALLOWED.search(sql)