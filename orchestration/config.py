"""orchestration/config.py

Single Pydantic BaseSettings class for all pipeline configuration.
Reads from environment variables and optional .env file.
"""
from __future__ import annotations

from typing import Optional

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class PipelineConfig(BaseSettings):
    """Centralised, validated configuration for the Dagster pipeline."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # SQLMesh
    sqlmesh_env: str = "dev"
    """SQLMesh environment: dev, prod, staging, etc."""

    # Quack
    enable_quack: bool = False
    """Enable Quack client-server mode (DuckDB >= v1.5.2 beta)."""
    quack_host: str = "localhost"
    """Quack server hostname."""
    quack_port: int = 9494
    """Quack server TCP port."""
    quack_token: str = ""
    """Quack shared authentication token (min 4 chars when enabled)."""

    # CSV export
    enable_csv_export: bool = False
    """Write CSV exports to data/exports/csv/<env>/"""
    csv_delimiter: str = ","
    """Delimiter character for CSV files."""

    # Parquet export
    enable_parquet_export: bool = False
    """Write Parquet exports to data/exports/parquet/<env>/"""
    parquet_compression: str = "snappy"
    """Parquet compression codec: snappy, gzip, brotli, zstd, lz4, none."""

    # Export dispatch
    export_background: bool = False
    """Fire-and-forget exports in a background thread instead of blocking."""
    export_timeout_seconds: int = 300
    """Timeout for blocking export operations."""

    # DuckDB resource override
    duckdb_path: Optional[str] = None
    """Override the default env-aware serving DB path (serving_dev.db / serving.db)."""

    @model_validator(mode="after")
    def validate_quack(self) -> "PipelineConfig":
        if self.enable_quack and not self.quack_token:
            raise ValueError("QUACK_TOKEN is required when ENABLE_QUACK=true")
        if not (1 <= self.quack_port <= 65535):
            raise ValueError("QUACK_PORT must be between 1 and 65535")
        return self