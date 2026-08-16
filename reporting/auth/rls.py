"""
reporting/auth/rls.py

Row-level security for the reporting app.

STRATEGY
--------
Every page reaches the database through exactly one function:
`reporting.utils.db.query()`. That is the enforcement point. Rather than
sprinkling `AND subregion IN (...)` through ~30 query builders in queries.py
(and inevitably missing one, and having no answer at all for the free-form
"Ask Your Data" page), the SQL is rewritten at execution time:

    FROM v_sales_base                  -- what the page wrote
    FROM (SELECT * FROM v_sales_base
          WHERE subregion IN ('Douala Nord')) AS v_sales_base   -- what runs

Because it happens below the query builders, a page physically cannot bypass
it -- including LLM-generated SQL.

WHO THE USERS ARE
-----------------
Salespeople are not report consumers. Three audiences:

  * report owner / data analyst  -> role "admin"  (unrestricted)
  * Regional Business Manager    -> role "rbm"    (all regions; scope by
                                    `regions` only if you ever split the role)
  * supervisor                   -> role "supervisor", scoped by `subregions`

`subregions` is the recommended supervisor scope, not `supervisors`. Reason:
`subregion` is present on every view the app actually reads AND on the KP-SD
sell-in chain, whereas `supervisor_name` is missing from v_regional_kpi,
v_product_kpi, v_innovation_kpi, v_client_kpi and (pre-patch) every KP-SD
view. Scoping on `supervisors` therefore fails closed on pages that scoping
on `subregions` handles cleanly. Add `supervisors` on top only when one
subregion has more than one supervisor and you need to split them.

FAIL-CLOSED
-----------
If a scoped user touches an object with no column for one of their restricted
dimensions, the query is REFUSED, not silently widened. See
`UNKNOWN_OBJECT_POLICY` and `MISSING_DIMENSION_POLICY`.

HONEST LIMITS
-------------
1. Application-level RLS. Anyone with the serving .db file on disk reads
   everything. Proportionate for a LAN dev deployment; not a substitute for
   DB-level policy.
2. The rewriter is regex-based, not a SQL parser. It matches identifiers after
   FROM/JOIN and would mis-handle an object name inside a string literal
   (`WHERE note = 'moved from v_sales_base'`). No query in this app does that.
   Swap in `sqlglot` if that ever changes -- the interface stays identical.
3. Scope values are inlined as SQL literals, not bound parameters, because
   injecting `?` placeholders would corrupt the positional order of every
   parameter the caller already supplied. They come from server-side
   secrets.toml, and `_sql_literal()` escapes quotes -- but do not extend this
   to accept values typed by users.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

class RLSError(RuntimeError):
    """Raised when a query cannot be safely scoped for the current user."""


# ---------------------------------------------------------------------------
# Indirect scoping
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Via:
    """Scope an object that lacks the column, through one that has it.

    fact_targets has no `subregion`, but it has `salesperson_id`, and
    dim_salesperson maps salesperson_id -> subregion. Produces:

        salesperson_id IN (SELECT salesperson_id FROM bi.dim_salesperson
                           WHERE subregion IN ('Douala Nord'))

    Caveat: the dimensions are SCD Type 2, so the subquery matches a key that
    was EVER in scope, not one that is in scope right now. For a supervisor
    boundary that errs generous: a rep who moved out of the subregion last
    quarter still resolves. Add `AND valid_to IS NULL` for strict
    point-in-time behaviour -- but then historical rows for a moved rep vanish
    from their old supervisor's view, which is usually not what you want.
    """
    local_column: str
    source: str
    source_key: str
    source_column: str


_DIM_SP = "dim_salesperson"
_DIM_SD = "dim_clientsd"


# ---------------------------------------------------------------------------
# The scope map -- built from serving/templates/bi_views.sql and the gold
# models, then verified with `python -m reporting.auth.introspect`.
# ---------------------------------------------------------------------------
# Keys are bare object names; any schema prefix is ignored when matching.
# Values map a Principal scope dimension -> a column name, or a Via(...).
#
# Entries marked [PATCH] require bi_views_rls_patch.sql. Without it they raise
# a DuckDB binder error instead of a clean RLSError -- run introspect after
# any bi_views.sql edit.

SCOPE_COLUMNS: dict[str, dict[str, str | Via]] = {

    # ======================================================================
    # Views the Streamlit app actually reads.
    # queries.py touches only these four; everything below exists for
    # "Ask Your Data" and for whatever gets built next.
    # ======================================================================

    # Fully denormalised -- every dimension is a direct column.
    "v_sales_base": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
        "clients": "clientsd_id",
    },
    # No clientsd_id: active_clients is a COUNT(DISTINCT), not a key.
    "v_monthly_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
    },
    # sales_channel is dropped from the GROUP BY here; no client key.
    "v_weekly_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
    },
    # No sales_channel, no client key.
    "v_quarterly_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
    },

    # ======================================================================
    # Sell-in / KP-SD chain.
    # Scoped through the SD (dim_clientsd), which carries its own region,
    # subregion and supervisor_name -- note that the SD's supervisor is a
    # different attribute from the selling rep's supervisor. See the addendum.
    # ======================================================================
    "v_kp_sd_base": {
        "regions": "region",
        "subregions": "subregion",
        "clients": "clientsd_id",
        "supervisors": "supervisor_name",        # [PATCH]
    },
    "v_kp_sd_monthly_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "clients": "clientsd_id",
        "supervisors": "supervisor_name",        # [PATCH]
    },
    "v_kp_performance_kpi": {
        "regions": "region",
        "subregions": "subregion",               # [PATCH]
        "supervisors": "supervisor_name",        # [PATCH]
    },
    "v_sellin_sellout_kpi": {
        "clients": "clientsd_id",
        "regions": "region",                     # [PATCH]
        "subregions": "subregion",               # [PATCH]
        "supervisors": "supervisor_name",        # [PATCH]
    },

    # ======================================================================
    # Defined in bi_views.sql but not read by any page today. Mapped anyway:
    # "Ask Your Data" can reach them, and they are the obvious sources for
    # the pages still to be built.
    # ======================================================================
    "v_ytd_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
    },
    "v_salesperson_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
    },
    "v_targets_base": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
    },
    "v_yoy_comparison": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
    },
    # region + subregion only -- no supervisor_name, no salesperson_id.
    "v_regional_kpi": {
        "regions": "region",
        "subregions": "subregion",
    },
    "v_client_kpi": {
        "regions": "region",
        "subregions": "subregion",
        "clients": "clientsd_id",
    },
    # region only in the shipped view; [PATCH] adds subregion.
    "v_product_kpi": {
        "regions": "region",
        "subregions": "subregion",               # [PATCH]
    },
    "v_innovation_kpi": {
        "regions": "region",
        "subregions": "subregion",               # [PATCH]
    },

    # v_executive_summary is deliberately ABSENT. It is one row per period
    # with no dimension columns at all, so it cannot be scoped -- a supervisor
    # reading it would see national totals. Leaving it unmapped turns that
    # into a clean refusal. queries.py never uses it (get_executive_kpis
    # builds its own aggregate over v_monthly_kpi + v_sales_base), so nothing
    # in the app breaks.

    # ======================================================================
    # Star schema (bi schema)
    # ======================================================================
    # fact_sales carries the salesperson attributes denormalised, so no Via is
    # needed -- that is what makes the filter dropdowns in db.py cheap.
    "fact_sales": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
        "clients": "clientsd_id",
    },
    "fact_targets": {
        "salespersons": "salesperson_id",
        "regions": Via("salesperson_id", _DIM_SP, "salesperson_id", "region"),
        "subregions": Via("salesperson_id", _DIM_SP, "salesperson_id", "subregion"),
        "supervisors": Via("salesperson_id", _DIM_SP, "salesperson_id", "supervisor_name"),
        "channels": Via("salesperson_id", _DIM_SP, "salesperson_id", "sales_channel"),
    },
    "fact_kp_sd": {
        "clients": "clientsd_id",
        "regions": Via("clientsd_id", _DIM_SD, "sd_id", "region"),
        "subregions": Via("clientsd_id", _DIM_SD, "sd_id", "subregion"),
        "supervisors": Via("clientsd_id", _DIM_SD, "sd_id", "supervisor_name"),
    },
    "dim_salesperson": {
        "regions": "region",
        "subregions": "subregion",
        "salespersons": "salesperson_id",
        "supervisors": "supervisor_name",
        "channels": "sales_channel",
    },
    # NOTE: the client key column here is `sd_id`, not `clientsd_id`.
    "dim_clientsd": {
        "regions": "region",
        "subregions": "subregion",
        "supervisors": "supervisor_name",
        "clients": "sd_id",
    },
}

# Objects every authenticated user may read in full -- no revenue, no owner
# dimension, nothing a supervisor shouldn't see.
UNSCOPED_OBJECTS: frozenset[str] = frozenset({
    "dim_date",
    "dim_products",
    # Serving-layer sync metadata: timestamps, durations, row counts. No
    # business dimension to scope on and nothing sensitive in it.
    "dim_product_price",
    "_sync_log",
    # Audit pass/fail history written by data_quality_full_report. Same
    # reasoning, and the Data Quality page is in every user's nav. Remove this
    # entry and add a mapping if you decide supervisors shouldn't see
    # cross-territory audit failures.
    "quality_trend",
    "columns", "tables", "schemata",   # information_schema probes

    # ── meta schema (2026-08) ────────────────────────────────────────────
    # Pipeline observability: batch outcomes, file arrivals, row counts,
    # schema drift. Operational, not commercial -- there is no region or
    # subregion to scope on. Without these entries UNKNOWN_OBJECT_POLICY
    # ("deny") refuses them for every scoped user, so the Data Quality page
    # renders only for admins.
    #
    # ⚠ meta.source_files and meta.extraction_coverage expose FILESYSTEM
    # PATHS and supervisor sheet names. That is fine for RBMs and
    # supervisors; think again before adding a role you would not show the
    # server's directory layout to.
    "ingestion_batches",
    "source_files",
    "landing_inventory",
    "column_inventory",
    "freshness",
    "extraction_coverage",

    # DuckDB catalog functions. db.object_exists() reads these, and _REF_RE
    # matches `FROM duckdb_views()` as an object reference. Without them a
    # scoped user's existence check raises RLSError, which silent=True
    # swallows into False -- so get_quarterly_summary would always take its
    # fallback path and never notice.
    "duckdb_views", "duckdb_tables", "duckdb_schemas", "duckdb_columns",
})

# Objects that are refused for scoped users ON PURPOSE, because they cannot be
# filtered at all. Listing them here is not a permission -- `predicate_for()`
# still raises. It exists so `introspect` reports them as an accepted decision
# rather than an unfinished map entry.
INTENTIONALLY_UNMAPPED: frozenset[str] = frozenset({
    # One row per period, no dimension columns whatsoever. A supervisor
    # reading it would see national totals. queries.py never touches it.
    "v_executive_summary",
})

# What to do when a scoped user references an object in neither map.
#   "deny"  -- refuse the query (default, and the correct posture)
#   "allow" -- let it through unfiltered (only while building the map out)
UNKNOWN_OBJECT_POLICY = "deny"

# What to do when an object IS mapped but has no column for one of the user's
# restricted dimensions.
#   "deny"  -- refuse (default)
#   "skip"  -- ignore that dimension for that object
MISSING_DIMENSION_POLICY = "deny"


# ---------------------------------------------------------------------------
# Predicate construction
# ---------------------------------------------------------------------------

def _sql_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    """`col IN ('a','b')`, or plain `FALSE` when the scope is empty.

    An empty tuple means "restricted to nothing", which must render as a whole
    clause -- `col IN ()` is a syntax error and `col FALSE` is nonsense.
    """
    if not values:
        return "FALSE"
    return f"{column} IN (" + ", ".join(_sql_literal(v) for v in values) + ")"


def predicate_for(obj: str, principal) -> str | None:
    """Return the WHERE predicate for `obj`, or None if no filtering is needed.

    Raises RLSError if the object cannot be safely scoped.
    """
    if principal.is_admin or not principal.is_scoped:
        return None

    bare = obj.split(".")[-1].strip('"').lower()

    if bare in UNSCOPED_OBJECTS:
        return None

    mapping = SCOPE_COLUMNS.get(bare)
    if mapping is None:
        if UNKNOWN_OBJECT_POLICY == "allow":
            return None
        raise RLSError(
            f"'{obj}' is not in the row-level-security map, so it cannot be "
            f"filtered for a scoped user ({principal.email}). Add it to "
            f"SCOPE_COLUMNS or UNSCOPED_OBJECTS in reporting/auth/rls.py. "
            f"Run `python -m reporting.auth.introspect` to see its columns."
        )

    clauses: list[str] = []
    for dim, allowed in principal.scope_items():
        target = mapping.get(dim)
        if target is None:
            if MISSING_DIMENSION_POLICY == "skip":
                continue
            raise RLSError(
                f"'{obj}' has no column for the '{dim}' restriction on "
                f"{principal.email}. Either expose that column in the view "
                f"(see bi_views_rls_patch.sql), add a Via(...) mapping, or "
                f"scope this user on a dimension the view carries -- "
                f"'subregions' is the widest-supported one."
            )
        if isinstance(target, Via):
            if not allowed:
                clauses.append("FALSE")
            else:
                inner = _in_clause(target.source_column, allowed)
                clauses.append(
                    f"{target.local_column} IN (SELECT {target.source_key} "
                    f"FROM {target.source} WHERE {inner})"
                )
        else:
            clauses.append(_in_clause(str(target), allowed))

    return " AND ".join(clauses) if clauses else None


# ---------------------------------------------------------------------------
# SQL rewriting
# ---------------------------------------------------------------------------

_REF_RE = re.compile(
    r"\b(FROM|JOIN)\s+((?:\"?[A-Za-z_]\w*\"?\.)?\"?[A-Za-z_]\w*\"?)",
    re.IGNORECASE,
)

# Tokens that can legally follow an object reference and are NOT an alias.
_NOT_AN_ALIAS = frozenset({
    "where", "group", "order", "having", "limit", "offset", "on", "using",
    "join", "left", "right", "inner", "full", "outer", "cross", "natural",
    "union", "except", "intersect", "window", "qualify", "select", "with",
    "anti", "semi", "asof", "positional", "tablesample", "sample", "returning",
})

_ALIAS_RE = re.compile(r"\s*(AS\s+)?([A-Za-z_]\w*)", re.IGNORECASE)


def _has_alias(sql: str, pos: int) -> bool:
    """True if an alias already follows the object reference at `pos`."""
    m = _ALIAS_RE.match(sql, pos)
    if not m:
        return False
    if m.group(1):          # explicit AS
        return True
    return m.group(2).lower() not in _NOT_AN_ALIAS


def apply_rls(sql: str, principal) -> str:
    """Rewrite `sql` so every scoped object is wrapped in a filtered subquery.

    Returns `sql` unchanged for admins and unscoped users.
    """
    if principal is None or principal.is_admin or not principal.is_scoped:
        return sql

    # Names introduced by CTEs are not database objects -- never rewrite them.
    cte_names = {m.lower() for m in
                 re.findall(r"(?:WITH|,)\s+([A-Za-z_]\w*)\s+AS\s*\(", sql, re.I)}

    out: list[str] = []
    cursor = 0
    # finditer over the ORIGINAL sql: injected text is never re-scanned, so a
    # Via subquery referencing dim_salesperson cannot recurse.
    for m in _REF_RE.finditer(sql):
        obj = m.group(2)
        bare = obj.split(".")[-1].strip('"').lower()
        if bare in cte_names:
            continue
        pred = predicate_for(obj, principal)
        if pred is None:
            continue

        replacement = f"{m.group(1)} (SELECT * FROM {obj} WHERE {pred})"
        if not _has_alias(sql, m.end()):
            replacement += f" AS {bare}"

        out.append(sql[cursor:m.start()])
        out.append(replacement)
        cursor = m.end()

    out.append(sql[cursor:])
    return "".join(out)


_DIM_LABEL = {
    "regions": "Region",
    "subregions": "Subregion",
    "salespersons": "Rep",
    "supervisors": "Team",
    "channels": "Channel",
    "clients": "SD",
}


def describe_scope(principal) -> str:
    """Human-readable one-liner for the sidebar badge."""
    if principal.is_admin:
        return "Full access"
    parts = []
    for dim, allowed in principal.scope_items():
        label = _DIM_LABEL.get(dim, dim)
        if not allowed:
            parts.append(f"{label}: none")
        elif len(allowed) <= 3:
            parts.append(f"{label}: {', '.join(allowed)}")
        else:
            parts.append(f"{label}: {len(allowed)} values")
    return " · ".join(parts) if parts else "All regions"