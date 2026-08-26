"""
tests/reporting/test_period_and_scope.py

One test per bug that shipped, in the same spirit as
tests/test_reporting_contracts.py: each of these would have failed before the
fix and passes after it. No lake, no Excel, no network.

    pytest tests/reporting/test_period_and_scope.py -v
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from reporting.utils.markup import clean_html
from reporting.utils.period import Period, fiscal_month_index, fiscal_year_of

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PAGES_DIR = PROJECT_ROOT / "reporting" / "app_pages"


# ===========================================================================
# BUG 1 -- the subregion filter was returned by the sidebar and consumed by
# nobody, so it filtered nothing for any user without an RLS predicate.
# ===========================================================================

# Every builder that reads a view carrying `subregion`. Keep in sync with
# SCOPE_COLUMNS in reporting/auth/rls.py -- it is the same audit.
BUILDERS_TAKING_SUBREGIONS = [
    "get_executive_kpis", "get_monthly_trend", "get_ytd_vs_prior_year",
    "get_top_regions", "get_regional_summary", "get_region_monthly_trend",
    "get_region_category_breakdown", "get_salesperson_ranking",
    "get_supervisor_summary", "get_category_performance",
    "get_product_ranking", "get_innovation_performance",
    "get_innovation_trend", "get_category_monthly_trend",
    "get_weekly_performance", "get_week_over_week", "get_region_weekly",
    "get_same_month_last_year", "get_region_yoy", "get_seasonality_heatmap",
    "get_category_month_heatmap", "get_quarterly_summary",
    "get_sellin_sellout", "get_destocked_flag_mismatches",
]


@pytest.mark.parametrize("name", BUILDERS_TAKING_SUBREGIONS)
def test_builder_accepts_subregions(name):
    from reporting.utils import queries

    fn = getattr(queries, name, None)
    assert fn is not None, f"queries.{name} is missing"
    assert "subregions" in inspect.signature(fn).parameters, (
        f"{name} cannot be given a subregion selection, so the sidebar filter "
        f"silently does nothing on any page that calls it."
    )


def test_subregion_predicate_reaches_the_sql():
    from reporting.utils.queries import _build_filters

    sql, params = _build_filters(year=2026, subregions=["Yaounde", "Est"])
    assert "subregion IN (?,?)" in sql
    assert params[-2:] == ["Yaounde", "Est"]


def test_scope_arguments_accept_a_list_not_only_a_scalar():
    """Pages used to pass `regions[0] if len(regions)==1 else None`, so
    selecting two regions filtered by neither."""
    from reporting.utils.queries import _build_filters

    sql, params = _build_filters(year=2026, regions=["Centre", "Littoral"])
    assert "region IN (?,?)" in sql
    assert "Centre" in params and "Littoral" in params


# ===========================================================================
# BUG 2 -- fiscal year. FY2026 = Oct-2025 .. Sep-2026.
# ===========================================================================

def test_fiscal_year_boundary():
    assert fiscal_year_of(2025, 9) == 2025
    assert fiscal_year_of(2025, 10) == 2026     # the boundary
    assert fiscal_year_of(2026, 9) == 2026
    assert fiscal_year_of(2026, 10) == 2027


def test_fiscal_period_spans_two_calendar_years():
    fy = Period(2026, fiscal=True)
    assert fy.calendar_years() == (2025, 2026)
    assert fy.contains(2025, 10)
    assert fy.contains(2026, 9)
    assert not fy.contains(2025, 9)             # belongs to FY2025
    assert not fy.contains(2026, 10)            # belongs to FY2027


def test_fiscal_clause_is_parenthesised():
    """An unparenthesised OR spliced into `WHERE ... AND <clause>` would widen
    the result to every row of one calendar year."""
    sql, params = Period(2026, fiscal=True).clause()
    assert sql.startswith("(") and sql.endswith(")")
    assert " OR " in sql
    assert 2025 in params and 2026 in params


def test_fiscal_month_lands_in_the_right_calendar_year():
    """October of FY2026 is calendar 2025-10, not 2026-10."""
    assert Period(2026, (10,), fiscal=True).calendar_years() == (2025,)
    assert Period(2026, (1,), fiscal=True).calendar_years() == (2026,)


def test_fiscal_quarter_one_is_october_to_december():
    from reporting.utils.period import FISCAL_QUARTER_MONTHS

    assert FISCAL_QUARTER_MONTHS[1] == [10, 11, 12]
    q1 = Period(2026, tuple(FISCAL_QUARTER_MONTHS[1]), fiscal=True)
    assert q1.calendar_years() == (2025,)


def test_prior_period_of_a_fiscal_year_is_the_prior_fiscal_year():
    prior = Period(2026, fiscal=True).prior()
    assert prior.fiscal and prior.year == 2025
    assert prior.calendar_years() == (2024, 2025)


def test_fiscal_months_sort_october_first():
    assert fiscal_month_index(10) == 1
    assert fiscal_month_index(9) == 12
    assert Period(2026, (1, 10, 5), fiscal=True).month_order() == [10, 1, 5]


def test_calendar_period_is_unchanged_and_int_still_works():
    """An unmigrated page passing a bare year must keep calendar behaviour."""
    sql, params = Period.coerce(2026).clause()
    assert sql == "(year = ?)" and params == [2026]
    assert Period.coerce(2026, 3).months == (3,)


# ===========================================================================
# BUG 3 -- the Sell-In page imported `main.`-prefixed names from
# reporting/utils/views.py and crashed with a Catalog Error.
# ===========================================================================

def _string_constants(tree: ast.AST) -> list[str]:
    """Every string literal EXCEPT the module docstring.

    AST-level rather than a text grep, deliberately: a page is allowed to
    explain in a comment or docstring what `main.` was and why it went away.
    What must not exist is a schema prefix in code that reaches SQL.
    """
    body = getattr(tree, "body", [])
    docstring_node = None
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        docstring_node = body[0].value
    return [
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and node is not docstring_node
    ]


def test_no_page_imports_the_legacy_views_module():
    offenders = []
    for page in sorted(PAGES_DIR.glob("*.py")):
        tree = ast.parse(page.read_text(encoding="utf-8"), page.name)
        for node in ast.walk(tree):
            module = getattr(node, "module", "") or ""
            if isinstance(node, ast.ImportFrom) and module.endswith("utils.views"):
                offenders.append(page.name)
            if isinstance(node, ast.Import) and any(
                a.name.endswith("utils.views") for a in node.names
            ):
                offenders.append(page.name)
    assert not offenders, (
        f"{offenders} import reporting.utils.views, whose constants carry a "
        f"`main.` prefix from the serving.db era. `main` is DuckDB's default "
        f"schema, not the lake."
    )


def test_no_page_hardcodes_a_schema_prefix():
    """Object names stay bare: rls.py keys on bare names, and SQLMesh
    namespaces schemas per environment (bi__dev vs bi)."""
    offenders = []
    for page in sorted(PAGES_DIR.glob("*.py")):
        tree = ast.parse(page.read_text(encoding="utf-8"), page.name)
        for literal in _string_constants(tree):
            for prefix in ("main.v_", "bi__dev.", "marts__dev."):
                if prefix in literal:
                    offenders.append(f"{page.name}: {prefix}")
    assert not offenders, offenders


def test_every_page_call_binds_to_its_builder_signature():
    """A renamed keyword in a page fails when a user opens the tab, not at
    import. Bind every call site statically instead."""
    from reporting.utils import queries

    sentinel = object()
    problems = []
    for page in sorted(PAGES_DIR.glob("*.py")):
        tree = ast.parse(page.read_text(encoding="utf-8"), page.name)
        imported = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "reporting.utils.queries"
            for alias in node.names
        }
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
                continue
            if node.func.id not in imported:
                continue
            fn = getattr(queries, node.func.id, None)
            if fn is None:
                problems.append(f"{page.name}:{node.lineno} queries.{node.func.id} missing")
                continue
            try:
                inspect.signature(fn).bind(
                    *([sentinel] * len(node.args)),
                    **{kw.arg: sentinel for kw in node.keywords if kw.arg},
                )
            except TypeError as exc:
                problems.append(f"{page.name}:{node.lineno} {node.func.id}: {exc}")
    assert not problems, problems


# ===========================================================================
# BUG 4 -- hand-built HTML rendered as literal text, because CommonMark reads
# a 4-space indent as a code block and a blank line as the end of an HTML
# block.
# ===========================================================================

def test_clean_html_removes_indentation_and_blank_lines():
    # Exactly the shape the Product Detail Table and Weekly Breakdown built.
    markup = """
                <tr style="border-bottom:1px solid #1F2937;">
                    <td>Choconut 9,7kg</td>
                </tr>

                <tr style="border-bottom:1px solid #1F2937;">
                    <td>Mambo Coffret</td>
                </tr>
            """
    out = clean_html(markup)
    assert "\n" not in out
    assert not out.startswith(" ")
    assert "    " not in out          # nothing Markdown can read as a code block
    assert out.count("<tr") == 2
    assert "Choconut 9,7kg" in out and "Mambo Coffret" in out


def test_clean_html_does_not_weld_wrapped_text_together():
    """Joined with a space, not with nothing: a value wrapped across two source
    lines must not come out as one word."""
    assert clean_html("<td>\n  105.2M\n  FCFA\n</td>") == "<td> 105.2M FCFA </td>"


def test_pages_do_not_pass_multiline_markup_to_st_markdown():
    """Single-line spacers are fine; multi-line f-strings are the bug."""
    offenders = []
    for page in sorted(PAGES_DIR.glob("*.py")):
        tree = ast.parse(page.read_text(encoding="utf-8"), page.name)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "markdown"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            # What matters is whether the VALUE contains a newline, not
            # whether the call spans lines: implicit concatenation of
            # single-line literals is fine and common in this codebase.
            pieces = (
                [first] if isinstance(first, ast.Constant)
                else first.values if isinstance(first, ast.JoinedStr)
                else []
            )
            if any(isinstance(p, ast.Constant) and isinstance(p.value, str)
                   and "\n" in p.value for p in pieces):
                offenders.append(f"{page.name}:{node.lineno}")
    assert not offenders, (
        f"{offenders} pass multi-line markup to st.markdown. Use "
        f"reporting.utils.markup.html_block / html_table instead."
    )
