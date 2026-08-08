# reporting/utils/ask.py
"""
Text-to-SQL helper for the "Ask Your Data" page.

Fixes applied (reporting-layer review):
  - `is_safe_sql()` existed but was never called anywhere in the app (see
    reporting/pages/7_Ask_Data.py) — the LLM's generated SQL was executed
    directly with no guard at all. It is now called from the page before
    execution.
  - `is_safe_sql()` only checked for a small keyword blocklist and didn't
    stop stacked statements (`SELECT ...; DROP ...`) or DuckDB table
    functions that read arbitrary files from disk (`read_csv`, `read_parquet`,
    `glob`, etc.) — those aren't blocked by a read-only *connection* to the
    serving DB, since they read the filesystem directly rather than writing
    to the database. Both are now blocked.
  - Added `enforce_row_limit()` so the "500 rows max" rule from SYSTEM_PROMPT
    is actually guaranteed at the SQL layer instead of relying on the model
    to comply with the instruction every time.
  - Code-fence stripping in `text_to_sql()` no longer assumes the *last*
    line of the response is the closing ``` fence; it strips fences with a
    regex instead so a real line of SQL can't be silently dropped.

Note: these are defence-in-depth measures for a single-user local tool, not
a substitute for running against a least-privilege, read-only role scoped to
the `bi`/BI-views schema if this is ever exposed beyond a trusted user.
"""
from __future__ import annotations
import json
import anthropic
import pandas as pd
import streamlit as st
import re

_client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

# Statements that must never appear in LLM-generated analytics SQL.
_DISALLOWED = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|CREATE|ALTER|TRUNCATE|COPY|EXPORT|IMPORT|"
    r"ATTACH|DETACH|PRAGMA|INSTALL|LOAD|CALL|SET|VACUUM|CHECKPOINT|GRANT|REVOKE)\b",
    re.IGNORECASE,
)

# DuckDB table functions that read directly from the filesystem/network
# rather than from the serving DB — a read-only DB connection does not
# stop these, so they need their own guard.
_FILE_FUNCS = re.compile(
    r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|glob|"
    r"sqlite_scan|postgres_scan|mysql_scan|iceberg_scan|delta_scan)\s*\(",
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


def _strip_code_fences(sql: str) -> str:
    """Strip a leading/trailing markdown code fence if present.

    FIX: the previous version assumed the model's *last* line was always the
    closing ``` fence and unconditionally dropped it (`split("\\n")[1:-1]`).
    If the model ever forgot the closing fence, or added trailing commentary,
    a real line of SQL was silently discarded. This strips fences by pattern
    instead of by position.
    """
    s = sql.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"```\s*$", "", s)
    return s.strip()


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
    return _strip_code_fences(sql)


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
    """Defence-in-depth guard for LLM-generated SQL before it is executed.

    Rejects anything that isn't a single, unstacked SELECT/CTE statement,
    contains a disallowed keyword, or calls a filesystem/network-reading
    table function.
    """
    stripped = sql.strip().rstrip(";").strip()
    if not stripped:
        return False
    if ";" in stripped:  # reject stacked statements
        return False
    if not stripped.upper().startswith(("SELECT", "WITH")):
        return False
    if _DISALLOWED.search(stripped):
        return False
    if _FILE_FUNCS.search(stripped):
        return False
    return True


def enforce_row_limit(sql: str, limit: int = 500) -> str:
    """Wrap the query so the row cap in SYSTEM_PROMPT is guaranteed rather
    than left to the model's compliance with the instruction.
    """
    cleaned = sql.strip().rstrip(";")
    return f"SELECT * FROM (\n{cleaned}\n) AS _ask_data_subq\nLIMIT {limit}"
