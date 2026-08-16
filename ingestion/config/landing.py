"""
Landing configuration.

ONE LAKE. `landing` is a schema inside the same DuckLake catalog SQLMesh
already uses (`sales_lakehouse` in sqlmesh/config.yaml), not a separate
catalog. That is what lets raw.* models read landing.* directly, with no
second ATTACH and no cross-catalog join.

The paths below read the SAME environment variables as sqlmesh/config.yaml
(DUCKLAKE_CATALOG_PATH, PARQUET_PATH). If those drift apart, ingestion writes
to one lake and SQLMesh reads another — which fails loudly and immediately
(raw models find no tables), not silently.

WRITE ORDERING: the DuckLake catalog is a DuckDB file, so it takes one writer
at a time. All landing writes happen in a single asset (`landing_load`) with
a single connection that closes before SQLMesh opens the lake. Do not add a
second concurrent writer without moving the catalog to PostgreSQL first.
"""
from __future__ import annotations

import os
from pathlib import Path

from shared.env import load_env, resolve_path
from shared.paths import WAREHOUSE_DIR

# Import-time, before any os.getenv below. SQLMesh loads .env itself; plain
# Python does not, so without this ingestion and SQLMesh read different values
# for the same variable names -- and the mismatch is silent.
load_env()

CATALOG_ALIAS = "sales_lakehouse"   # must match sqlmesh/config.yaml catalogs:
LANDING_SCHEMA = "landing"
REGISTRY_TABLE = "file_registry"

DUCKLAKE_CATALOG_PATH = resolve_path(
    os.getenv("DUCKLAKE_CATALOG_PATH"), WAREHOUSE_DIR / "catalog.ducklake"
)
PARQUET_PATH = resolve_path(
    os.getenv("PARQUET_PATH"), WAREHOUSE_DIR / "parquet_storage"
)


def _flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    return default if raw is None else raw.strip().lower() in {"1", "true", "yes", "on"}


# Skip a file whose sha256 is already registered as ingested. On by default:
# re-running the pipeline over an unchanged input directory should be a no-op
# rather than doubling the landing history.
LANDING_SKIP_DUPLICATE_FILES = _flag("LANDING_SKIP_DUPLICATE_FILES", True)

# Add columns present in the frame but missing from the landing table.
# False = raise instead, for a frozen contract.
LANDING_EVOLVE_SCHEMA = _flag("LANDING_EVOLVE_SCHEMA", True)

# landing table name per source_type. Keys match sources.yaml source types;
# values match the raw.* model that reads them.
# ─────────────────────────────────────────────────────────────────────────
# Landing schema DDL
#
# Every table in the landing schema is declared here, and LandingWriter
# creates them all on connect. The two audit tables are WRITTEN by
# orchestration/assets/data_quality.py, not by ingestion -- but they are
# CREATED here, because the schema owner should own its DDL and because of a
# hard ordering constraint:
#
#   meta.audit_results and meta.audit_failures are SQLMesh VIEWS over these
#   tables. DuckDB validates a view's references at CREATE VIEW, so `sqlmesh
#   plan` fails outright if the tables do not exist. The tables are written by
#   an asset check that runs AFTER a successful plan. Nothing would ever run.
#
# Creating them empty at ingestion time breaks the cycle: layer 1 always runs
# before layer 2, so by the time SQLMesh plans, the tables are there.
# ─────────────────────────────────────────────────────────────────────────

REGISTRY_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.file_registry (
    file_sha256      VARCHAR,
    source_type      VARCHAR,
    source_path      VARCHAR,
    source_file      VARCHAR,
    file_size_bytes  BIGINT,
    file_mtime       TIMESTAMP,
    batch_id         VARCHAR,
    extractor        VARCHAR,
    landing_table    VARCHAR,
    row_count        BIGINT,
    status           VARCHAR,   -- ingested | skipped_duplicate | failed | retired
    error            VARCHAR,
    registered_at    TIMESTAMP
)
"""

AUDIT_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.audit_results (
    run_at        TIMESTAMP,
    run_id        VARCHAR,
    environment   VARCHAR,
    audit         VARCHAR,
    entity        VARCHAR,
    question      VARCHAR,
    severity      VARCHAR,
    status        VARCHAR,      -- PASSED | FAILED | ERROR
    rows_checked  BIGINT,
    rows_failing  BIGINT,
    files_failing BIGINT,
    error         VARCHAR
)
"""

AUDIT_FAILURES_DDL = """
CREATE TABLE IF NOT EXISTS {schema}.audit_failures (
    run_at         TIMESTAMP,
    run_id         VARCHAR,
    audit          VARCHAR,
    entity         VARCHAR,
    source_file    VARCHAR,
    sheet_name     VARCHAR,
    source_row_num BIGINT,
    detail         VARCHAR       -- the business columns, as JSON
)
"""

LANDING_DDL = (REGISTRY_DDL, AUDIT_RESULTS_DDL, AUDIT_FAILURES_DDL)

# Tables in the landing schema that hold OBSERVABILITY rather than source data.
# They carry no _source_path/_file_sha256, so anything that walks the schema
# looking for ingested rows -- LandingWriter.summary(), current_rows() -- must
# skip them. Keeping the list here means adding a fourth observability table
# does not require finding every place that assumed "registry plus data".
OBSERVABILITY_TABLES = frozenset({"file_registry", "audit_results", "audit_failures"})


LANDING_TABLES = {
    "sales": {"sales_data": None},
    "targets": {"targets_data": None},
    "references": {
        "salesteam_data": "ref_salesteam",
        "products_data": "ref_products",
        "clientsd_data": "ref_clients_sd",
        "kp_sku_mapping_data": "ref_kp_sku_mapping",
    },
    "kp_sd": {
        "kp_sd_destocke_data": None,
        "kp_sd_non_destocke_data": None,
    },
}