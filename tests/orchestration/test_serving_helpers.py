# tests/orchestration/test_serving_helpers.py
"""Tests for serving.py helper functions that read env-vars."""

import os
from unittest.mock import MagicMock
import pytest
from orchestration.assets.serving import _build_config, _build_export_bus


@pytest.fixture
def fake_context():
    """Minimal AssetExecutionContext stand-in."""
    ctx = MagicMock()
    ctx.log.info = MagicMock()
    ctx.log.warning = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# _build_config
# ---------------------------------------------------------------------------

def test_build_config_file_swap(fake_context, monkeypatch):
    """When Quack is disabled we get a plain ServingConfig with quack=None."""
    monkeypatch.delenv("ENABLE_QUACK", raising=False)
    monkeypatch.delenv("QUACK_TOKEN", raising=False)

    config = _build_config("dev", fake_context)

    assert config.environment == "dev"
    assert config.quack is None


def test_build_config_quack_enabled(fake_context, monkeypatch):
    """All Quack env-vars forwarded into ServingConfig.quack."""
    monkeypatch.setenv("ENABLE_QUACK", "true")
    monkeypatch.setenv("QUACK_TOKEN", "secret-token")
    monkeypatch.setenv("QUACK_HOST", "quack.example.com")
    monkeypatch.setenv("QUACK_PORT", "9999")

    config = _build_config("prod", fake_context)

    assert config.environment == "prod"
    assert config.quack is not None
    assert config.quack.host == "quack.example.com"
    assert config.quack.port == 9999
    assert config.quack.token == "secret-token"


def test_build_config_quack_missing_token(fake_context, monkeypatch):
    """Missing QUACK_TOKEN falls back to file-swap mode with a warning."""
    monkeypatch.setenv("ENABLE_QUACK", "true")
    monkeypatch.delenv("QUACK_TOKEN", raising=False)

    config = _build_config("dev", fake_context)

    assert config.quack is None
    fake_context.log.warning.assert_called_once()


# ---------------------------------------------------------------------------
# _build_export_bus
# ---------------------------------------------------------------------------

def test_build_export_bus_none_enabled(fake_context, monkeypatch):
    """When no export env-vars are set the bus is None."""
    monkeypatch.delenv("ENABLE_CSV_EXPORT", raising=False)
    monkeypatch.delenv("ENABLE_PARQUET_EXPORT", raising=False)

    bus = _build_export_bus("dev", fake_context)

    assert bus is None


def test_build_export_bus_csv_only(fake_context, monkeypatch):
    """CSV env-var wires a single CsvExporter subscriber."""
    monkeypatch.setenv("ENABLE_CSV_EXPORT", "true")
    monkeypatch.setenv("CSV_DELIMITER", ";")
    monkeypatch.delenv("ENABLE_PARQUET_EXPORT", raising=False)

    bus = _build_export_bus("dev", fake_context)

    assert bus is not None
    assert len(bus.subscribers) == 1
    assert type(bus.subscribers[0]).__name__ == "CsvExporter"


def test_build_export_bus_parquet_only(fake_context, monkeypatch):
    """Parquet env-var wires a single ParquetExporter subscriber."""
    monkeypatch.delenv("ENABLE_CSV_EXPORT", raising=False)
    monkeypatch.setenv("ENABLE_PARQUET_EXPORT", "true")
    monkeypatch.setenv("PARQUET_COMPRESSION", "gzip")

    bus = _build_export_bus("dev", fake_context)

    assert bus is not None
    assert len(bus.subscribers) == 1
    assert type(bus.subscribers[0]).__name__ == "ParquetExporter"


def test_build_export_bus_both_exporters(fake_context, monkeypatch):
    """Both env-vars → two subscribers in the bus."""
    monkeypatch.setenv("ENABLE_CSV_EXPORT", "true")
    monkeypatch.setenv("ENABLE_PARQUET_EXPORT", "true")

    bus = _build_export_bus("dev", fake_context)

    assert bus is not None
    assert len(bus.subscribers) == 2
    names = {type(s).__name__ for s in bus.subscribers}
    assert names == {"CsvExporter", "ParquetExporter"}