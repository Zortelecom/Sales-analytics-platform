# tests/assets/test_serving_sync.py
"""End-to-end test for the serving-layer sync."""

import duckdb
import pytest
from pathlib import Path

from serving.config import ServingConfig
from serving.sync import ServingLayerSync


def test_full_serving_sync(monkeypatch, tmp_path, ducklake_catalog):
    """
    Run ServingLayerSync against a real DuckDB catalog file.

    Two monkey-patches are required because CI does not have the
    DuckLake extension installed:
      1. _ensure_ducklake_extension → no-op
      2. Strip (TYPE DUCKLAKE) from ATTACH statements so a plain
         DuckDB file can be attached.
    """
    # 1. Bypass DuckLake extension loading
    monkeypatch.setattr(
        "serving.sync._ensure_ducklake_extension",
        lambda conn: None,
    )

    # 2. Allow ATTACH of a plain DuckDB file by removing TYPE DUCKLAKE
    _orig_execute = duckdb.DuckDBPyConnection.execute

    def _patched_execute(self, sql, *args, **kwargs):
        if isinstance(sql, str) and "TYPE DUCKLAKE" in sql:
            sql = sql.replace(" (TYPE DUCKLAKE)", "")
        return _orig_execute(self, sql, *args, **kwargs)

    monkeypatch.setattr(duckdb.DuckDBPyConnection, "execute", _patched_execute)

    # Build and normalise config inside the temp project tree
    config = ServingConfig(environment="dev")
    config.ducklake_path = str(ducklake_catalog)
    config.apply_views = False          # no BI views template in this test
    config.normalize(project_root=tmp_path)

    # Run the sync
    sync = ServingLayerSync(config)
    summary = sync.sync(dry_run=False)

    # ── summary assertions ─────────────────────────────────────────────────
    assert summary["status"] == "success"
    assert summary["tables_total"] == 2
    assert summary["tables_succeeded"] == 2
    assert summary["tables_failed"] == 0

    # ── serving DB assertions ──────────────────────────────────────────────
    serving_path = Path(config.serving_path)
    assert serving_path.exists(), "serving_dev.db was not created"

    conn = duckdb.connect(str(serving_path))
    tables = conn.execute("""
        SELECT table_name
        FROM information_schema.tables
        WHERE table_schema = 'bi'
          AND table_name IN ('fact_sales', 'dim_products')
    """).fetchall()
    assert {t[0] for t in tables} == {"fact_sales", "dim_products"}

    assert conn.execute("SELECT COUNT(*) FROM bi.fact_sales").fetchone()[0] == 10
    assert conn.execute("SELECT COUNT(*) FROM bi.dim_products").fetchone()[0] == 10
    conn.close()

    # ── validation ─────────────────────────────────────────────────────────
    assert sync.validate_serving_db() is True