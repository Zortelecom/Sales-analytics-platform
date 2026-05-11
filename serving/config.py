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
class ServingConfig:
    """
    Configuration for the serving layer sync process.
    
    Handles SQLMesh environment alignment, DuckLake connection,
    and dual-format exports (CSV + Parquet).
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
    views_template_path: Optional[str] = None  # e.g., "serving/templates/bi_views.sql"
    
    # =========================================================================
    # Internal: Temp and Backup
    # =========================================================================
    temp_path: Optional[str] = None
    
    # =========================================================================
    # Exports: External Formats
    # =========================================================================
    # CSV exports (for compatibility, BI tools, Excel)
    csv_export: ExportConfig = field(default_factory=lambda: ExportConfig(
        enabled=True,
        path=None,  # Set by normalize()
        compression=None
    ))
    
    # Parquet exports (for analytics, data science, efficient storage)
    parquet_export: ExportConfig = field(default_factory=lambda: ExportConfig(
        enabled=True,
        path=None,  # Set by normalize()
        compression="snappy"  # Default: fast with good compression
    ))

    def normalize(self, project_root: Optional[Path] = None) -> None:
        """
        Align serving config with SQLMesh environment conventions.
        
        Sets up environment-specific paths for:
        - DuckLake catalog
        - Serving database
        - CSV export folder
        - Parquet export folder
        
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

        # Resolve serving database paths
        self.serving_path = str(self.serving_path.resolve())
        self.temp_path = self.serving_path.replace(".db", "_temp.db")
        
        # =========================================================================
        # Setup Export Paths
        # =========================================================================
        
        # Handle legacy config attributes for backward compatibility
        # If legacy attributes are set, migrate them to new ExportConfig
        if self.csv_export_path and not self.csv_export.path:
            self.csv_export.path = self.csv_export_path
        if not self.csv_export.path:
            self.csv_export.path = str((root / csv_folder).resolve())
        self.csv_export.enabled = self.enable_csv_export
        
        if self.parquet_export_path and not self.parquet_export.path:
            self.parquet_export.path = self.parquet_export_path
        if not self.parquet_export.path:
            self.parquet_export.path = str((root / parquet_folder).resolve())
        self.parquet_export.enabled = self.enable_parquet_export
        
        # Validate compression for Parquet
        valid_compressions = [None, "snappy", "gzip", "brotli", "zstd", "lz4", "none"]
        if self.parquet_export.compression not in valid_compressions:
            raise ValueError(
                f"Invalid Parquet compression: {self.parquet_export.compression}. "
                f"Must be one of: {valid_compressions}"
            )
        
        # Setup BI Views Template Path
        # =========================================================================
        if self.apply_views and not self.views_template_path:
            # Default location relative to project root
            self.views_template_path = str(root / "serving/templates/bi_views.sql")
        if self.views_template_path:
            self.views_template_path = str((root / self.views_template_path).resolve())

    @property
    def enable_csv_export(self) -> bool:
        """Backward compatibility: delegate to csv_export.enabled"""
        return self.csv_export.enabled if self.csv_export else True
    
    @enable_csv_export.setter
    def enable_csv_export(self, value: bool) -> None:
        """Backward compatibility: delegate to csv_export.enabled"""
        if self.csv_export is None:
            self.csv_export = ExportConfig(enabled=value)
        else:
            self.csv_export.enabled = value

    @property
    def csv_export_path(self) -> Optional[str]:
        """Backward compatibility: delegate to csv_export.path"""
        return self.csv_export.path if self.csv_export else None
    
    @csv_export_path.setter
    def csv_export_path(self, value: Optional[str]) -> None:
        """Backward compatibility: delegate to csv_export.path"""
        if self.csv_export is None:
            self.csv_export = ExportConfig(path=value)
        else:
            self.csv_export.path = value

    @property
    def enable_parquet_export(self) -> bool:
        """Backward compatibility: delegate to parquet_export.enabled"""
        return self.parquet_export.enabled if self.parquet_export else True
    
    @enable_parquet_export.setter
    def enable_parquet_export(self, value: bool) -> None:
        """Backward compatibility: delegate to parquet_export.enabled"""
        if self.parquet_export is None:
            self.parquet_export = ExportConfig(enabled=value)
        else:
            self.parquet_export.enabled = value

    @property
    def parquet_export_path(self) -> Optional[str]:
        """Backward compatibility: delegate to parquet_export.path"""
        return self.parquet_export.path if self.parquet_export else None
    
    @parquet_export_path.setter
    def parquet_export_path(self, value: Optional[str]) -> None:
        """Backward compatibility: delegate to parquet_export.path"""
        if self.parquet_export is None:
            self.parquet_export = ExportConfig(path=value)
        else:
            self.parquet_export.path = value

    def get_export_summary(self) -> dict:
        """
        Get a summary of export configurations.
        
        Returns:
            Dictionary with export settings for both formats
        """
        return {
            "csv": {
                "enabled": self.csv_export.enabled if self.csv_export else False,
                "path": self.csv_export.path if self.csv_export else None,
                "compression": None
            },
            "parquet": {
                "enabled": self.parquet_export.enabled if self.parquet_export else False,
                "path": self.parquet_export.path if self.parquet_export else None,
                "compression": self.parquet_export.compression if self.parquet_export else None
            }
        }

    def __post_init__(self):
        """Ensure ExportConfig objects are initialized if None"""
        if self.csv_export is None:
            self.csv_export = ExportConfig(enabled=self.enable_csv_export)
        if self.parquet_export is None:
            self.parquet_export = ExportConfig(
                enabled=self.enable_parquet_export,
                compression="snappy"
            )