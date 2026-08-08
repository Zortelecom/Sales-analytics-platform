"""Serving layer configuration"""
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class ExportConfig:
    """
    Configuration for external format exports (CSV and Parquet).

    Attributes:
        enabled: Whether to enable exports
        path: Directory path for exports
        compression: Compression codec (for Parquet only)
    """
    enabled: bool = True
    path: Optional[str] = None
    compression: Optional[str] = None  # e.g., 'snappy', 'gzip', 'zstd' for Parquet


@dataclass
class QuackConfig:
    """
    Configuration for the Quack client-server protocol (DuckDB ≥ v1.5.2, beta).

    Quack turns DuckDB into a proper client-server database: multiple BI tools
    and the sync process can hold simultaneous read-write connections to the same
    serving.db without any file-lock contention.

    Why it matters for this serving layer:
    - Eliminates the temp-DB + atomic-file-swap dance: sync writes directly to
      the live server via ATTACH, so BI dashboards never see a partially-written
      state and never get locked out during the 6 AM pipeline run.
    - Removes the Python-DataFrame intermediary: data travels from DuckLake to
      the Quack server entirely inside DuckDB's vectorised engine, skipping the
      source.fetch_df() → target.register() → CTAS round-trip.
    - Replaces the manual LIMIT/OFFSET batching loop: DuckDB streams large
      result sets natively over the wire; no hand-written pagination needed.
    - Enables Streamlit, Power BI and any ad-hoc DuckDB session to share one
      live connection without "database is locked" errors.

    See: https://duckdb.org/quack/
    Note: Quack is in beta; stable release planned for DuckDB v2.0 (Sept 2026).
          Use `use_quack=False` (default) to keep the existing file-swap mode.

    Attributes:
        host: Hostname where the Quack server listens (default localhost).
        port: TCP port (default 9494 — DuckDB's default Quack port).
        token: Shared auth token. Minimum 4 characters. Must match between
               server (`quack_serve`) and all clients (`CREATE SECRET`).
        ssl:   Whether to use TLS. Requires a reverse-proxy (e.g. Caddy) in
               front; DuckDB does not terminate TLS natively.
        secret_name: DuckDB secret name used on client connections. Kept
                     internal but configurable to avoid conflicts with other
                     secrets in the same session.
    """
    host: str = "localhost"
    port: int = 9494
    token: str = "change_me_in_production"
    ssl: bool = False
    secret_name: str = "_quack_serving_secret"

    @property
    def uri(self) -> str:
        """Quack connection URI understood by ATTACH and quack_query()."""
        return f"quack:{self.host}:{self.port}"

    @property
    def catalog_alias(self) -> str:
        """Name of the ATTACH alias used in the sync connection."""
        return "_serving_remote"

    def validate(self) -> None:
        """Raise ValueError if the config is obviously wrong."""
        if len(self.token) < 4:
            raise ValueError(
                "Quack token must be at least 4 characters. "
                "Set a strong secret in production."
            )
        if not (1 <= self.port <= 65535):
            raise ValueError(f"Invalid Quack port: {self.port}")

    def __str__(self) -> str:
        return f"QuackConfig(uri={self.uri}, ssl={self.ssl})"


@dataclass
class ServingConfig:
    """
    Configuration for the serving layer sync process.

    Handles SQLMesh environment alignment, DuckLake connection,
    optional Quack client-server mode, and dual-format exports (CSV + Parquet).

    Quack mode vs. file-swap mode
    ──────────────────────────────
    File-swap (default, quack=None):
        Build serving.db in a temp file → atomic rename → BI tools reconnect.
        Simple, zero extra dependencies, but BI clients see a brief outage
        window and the process uses Python memory to ferry data.

    Quack mode (quack=QuackConfig(...)):
        A persistent DuckDB Quack server wraps serving.db.  The sync process
        ATTACHes to the live server and overwrites tables in-place via
        CREATE OR REPLACE TABLE — no temp file, no rename, no BI outage.
        Requires `INSTALL quack FROM core_nightly` on DuckDB ≥ v1.5.2.
    """

    # =========================================================================
    # SQLMesh Environment
    # =========================================================================
    environment: str = "dev"

    # =========================================================================
    # Source: DuckLake Catalog
    # =========================================================================
    ducklake_path: str = "data/warehouse/catalog.ducklake"
    source_catalog_alias: str = "sales_lakehouse"

    # Derived from environment (set by normalize())
    marts_schema: Optional[str] = None

    # =========================================================================
    # Target: Serving Database
    # =========================================================================
    serving_path: Optional[str] = None
    bi_schema: str = "bi"

    # Table filtering
    exclude_tables: Optional[List[str]] = None
    include_tables: Optional[List[str]] = None

    # =========================================================================
    # BI Views
    # =========================================================================
    apply_views: bool = True
    views_template_path: Optional[str] = None

    # =========================================================================
    # Internal: Temp and Backup (file-swap mode only)
    # =========================================================================
    temp_path: Optional[str] = None

    # =========================================================================
    # Quack client-server (optional; set to enable Quack mode)
    # =========================================================================
    quack: Optional[QuackConfig] = None

    # =========================================================================
    # Exports: External Formats
    # =========================================================================
    csv_export: ExportConfig = field(default_factory=lambda: ExportConfig(
        enabled=True,
        path=None,
        compression=None
    ))

    parquet_export: ExportConfig = field(default_factory=lambda: ExportConfig(
        enabled=True,
        path=None,
        compression="snappy"
    ))

    # =========================================================================
    # Derived helpers
    # =========================================================================

    @property
    def quack_enabled(self) -> bool:
        """True when Quack mode is active."""
        return self.quack is not None

    def normalize(self, project_root: Optional[Path] = None) -> None:
        """
        Align serving config with SQLMesh environment conventions.

        Sets up environment-specific paths for:
        - DuckLake catalog
        - Serving database (and temp path for file-swap mode)
        - CSV export folder
        - Parquet export folder

        Validates QuackConfig if present.

        Args:
            project_root: Project root directory (auto-detected if None)
        """
        root = project_root or Path(__file__).parent.parent

        # Resolve DuckLake path
        self.ducklake_path = str((root / self.ducklake_path).resolve())

        # Determine environment-specific settings
        if self.environment == "prod":
            self.marts_schema = "marts"
            self.serving_path = root / "data/warehouse/serving.db"
            csv_folder = "data/exports/csv/prod"
            parquet_folder = "data/exports/parquet/prod"
        else:
            self.marts_schema = f"marts__{self.environment}"
            self.serving_path = root / f"data/warehouse/serving_{self.environment}.db"
            csv_folder = f"data/exports/csv/{self.environment}"
            parquet_folder = f"data/exports/parquet/{self.environment}"

        self.serving_path = str(self.serving_path.resolve())
        # temp_path only used in file-swap mode, but always set for safety.
        # FIX: derive from the Path object instead of str.replace(".db", ...),
        # which silently no-ops when the path doesn't contain ".db".
        _serving = Path(self.serving_path)
        self.temp_path = str(_serving.with_name(f"{_serving.stem}_temp{_serving.suffix}"))

        # =========================================================================
        # Export Paths
        # =========================================================================
        # FIX: removed the self-referential conditions. The csv_export_path /
        # parquet_export_path properties proxy ExportConfig.path, so
        # `if self.csv_export_path and not self.csv_export.path` was always
        # False (dead code), and `self.csv_export.enabled = self.enable_csv_export`
        # was a no-op self-assignment (the property reads the same attribute).
        # What remains is the real intent: default the export folders when no
        # explicit path was configured.
        if not self.csv_export.path:
            self.csv_export.path = str((root / csv_folder).resolve())

        if not self.parquet_export.path:
            self.parquet_export.path = str((root / parquet_folder).resolve())

        valid_compressions = [None, "snappy", "gzip", "brotli", "zstd", "lz4", "none"]
        if self.parquet_export.compression not in valid_compressions:
            raise ValueError(
                f"Invalid Parquet compression: {self.parquet_export.compression}. "
                f"Must be one of: {valid_compressions}"
            )

        # =========================================================================
        # BI Views Template
        # =========================================================================
        if self.apply_views and not self.views_template_path:
            self.views_template_path = str(root / "serving/templates/bi_views.sql")
        if self.views_template_path:
            self.views_template_path = str((root / self.views_template_path).resolve())

        # =========================================================================
        # Quack validation
        # =========================================================================
        if self.quack:
            self.quack.validate()

    # =========================================================================
    # Backward-compatibility properties (delegate to ExportConfig sub-objects)
    # =========================================================================

    @property
    def enable_csv_export(self) -> bool:
        return self.csv_export.enabled if self.csv_export else True

    @enable_csv_export.setter
    def enable_csv_export(self, value: bool) -> None:
        if self.csv_export is None:
            self.csv_export = ExportConfig(enabled=value)
        else:
            self.csv_export.enabled = value

    @property
    def csv_export_path(self) -> Optional[str]:
        return self.csv_export.path if self.csv_export else None

    @csv_export_path.setter
    def csv_export_path(self, value: Optional[str]) -> None:
        if self.csv_export is None:
            self.csv_export = ExportConfig(path=value)
        else:
            self.csv_export.path = value

    @property
    def enable_parquet_export(self) -> bool:
        return self.parquet_export.enabled if self.parquet_export else True

    @enable_parquet_export.setter
    def enable_parquet_export(self, value: bool) -> None:
        if self.parquet_export is None:
            self.parquet_export = ExportConfig(enabled=value)
        else:
            self.parquet_export.enabled = value

    @property
    def parquet_export_path(self) -> Optional[str]:
        return self.parquet_export.path if self.parquet_export else None

    @parquet_export_path.setter
    def parquet_export_path(self, value: Optional[str]) -> None:
        if self.parquet_export is None:
            self.parquet_export = ExportConfig(path=value)
        else:
            self.parquet_export.path = value

    def get_export_summary(self) -> dict:
        return {
            "csv": {
                "enabled": self.csv_export.enabled if self.csv_export else False,
                "path": self.csv_export.path if self.csv_export else None,
                "compression": None,
            },
            "parquet": {
                "enabled": self.parquet_export.enabled if self.parquet_export else False,
                "path": self.parquet_export.path if self.parquet_export else None,
                "compression": self.parquet_export.compression if self.parquet_export else None,
            },
        }

    def __post_init__(self):
        if self.csv_export is None:
            self.csv_export = ExportConfig(enabled=True)
        if self.parquet_export is None:
            self.parquet_export = ExportConfig(enabled=True, compression="snappy")
