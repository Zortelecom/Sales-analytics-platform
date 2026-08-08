"""
Orchestration Assets Package
Exports all Dagster assets for the sales analytics pipeline
"""

from .file_discovery import discovered_files, files_to_process
from .preprocessing import preprocessed_files
from .ingestion import (
    current_batch_id,
    sales_seed,
    targets_seed,
    kp_sd_seed,
    references_seeds,
    seeds_metadata
)
from .transformation import sqlmesh_models, marts_validation
from .serving import serving_database, pipeline_complete

__all__ = [
    # File discovery
    "discovered_files",
    "files_to_process",

    # Preprocessing
    "preprocessed_files",

    # Ingestion
    "current_batch_id",
    "sales_seed",
    "targets_seed",
    "kp_sd_seed",
    "references_seeds",
    "seeds_metadata",

    # Transformation
    "sqlmesh_models",
    "marts_validation",

    # Serving
    "serving_database",
    "pipeline_complete",
]
