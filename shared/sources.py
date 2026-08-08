"""
Authoritative source registry.

This module is the ONLY place that parses ingestion/config/sources.yaml.
Every consumer -- FileDiscovery, ExcelPreprocessor, the Dagster preprocessing
and ingestion assets, the extractors -- imports the parsed object from here.

WHAT THIS REPLACED (2026-08)
────────────────────────────
Before this module, a source's identity was declared in four places:

  1. sources.yaml top-level blocks (directory / file_pattern / table_prefix)
  2. sources.yaml `sources.local_sync.folders` (source directory)
  3. sources.yaml `processing:` block (sheet rules)  -- loaded, never read
  4. settings.py SourceConfig                        -- one named field per source
  5. shared/paths.py INPUT_PATHS                     -- one key per source
  6. preprocessing.py                                -- if/elif on source_type

...and they had already drifted: the yaml said the sales delete pattern was
"Synthese*" while preprocessing.py used "Synthese *"; the yaml required four
reference sheets that do not exist. sources.yaml is now the single declaration
and drift is caught by tests/test_sources_config.py.

CACHING
───────
load_sources() is lru_cached, so editing sources.yaml requires a process
restart (a Dagster code-location reload is enough). This is deliberate: a
config that can change mid-run is worse than one that cannot.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from shared.paths import DATA_DIR, PROJECT_ROOT

SOURCES_YAML = PROJECT_ROOT / "ingestion" / "config" / "sources.yaml"


class ProcessingSpec(BaseModel):
    """Sheet-level rules applied by ExcelPreprocessor before extraction."""
    model_config = ConfigDict(extra="forbid")

    delete_sheets_pattern: Optional[str] = None
    required_sheets: Optional[List[str]] = None


class TableSpec(BaseModel):
    """A named Excel table inside a multi-table workbook (references only)."""
    model_config = ConfigDict(extra="forbid")

    name: str
    required_columns: List[str] = Field(default_factory=list)


class SourceSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str
    source_dir: Path          # resolved: base_path / source_folder
    input_dir: Path           # resolved: data/input/ / input_subdir
    file_pattern: str
    table_prefix: str
    required_columns: List[str] = Field(default_factory=list)
    tables: Optional[List[TableSpec]] = None
    processing: ProcessingSpec = Field(default_factory=ProcessingSpec)

    def required_columns_for(self, table_name: str) -> List[str]:
        """Column contract for a named table, falling back to the source-level set."""
        for table in self.tables or []:
            if table.name.lower() == table_name.lower():
                return table.required_columns
        return self.required_columns


class ValidationSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_file_size_mb: int = 50
    allowed_extensions: List[str] = Field(default_factory=lambda: [".xlsx", ".xlsm"])
    check_corruption: bool = True


class SourcesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_path: Path
    validation: ValidationSpec
    sources: Dict[str, SourceSpec]

    def __getitem__(self, source_type: str) -> SourceSpec:
        try:
            return self.sources[source_type]
        except KeyError:
            raise KeyError(
                f"Unknown source_type {source_type!r}. "
                f"Declared sources: {sorted(self.sources)}. "
                f"Add a block under `sources:` in {SOURCES_YAML}."
            ) from None

    def __contains__(self, source_type: str) -> bool:
        return source_type in self.sources

    @property
    def source_types(self) -> List[str]:
        return sorted(self.sources)

    @property
    def input_paths(self) -> Dict[str, Path]:
        """source_type -> data/input/<subdir>. Replaces shared.paths.INPUT_PATHS."""
        return {k: s.input_dir for k, s in self.sources.items()}

    @property
    def source_paths(self) -> Dict[str, Path]:
        """source_type -> synced source folder. Replaces settings.SourceConfig fields."""
        return {k: s.source_dir for k, s in self.sources.items()}


@lru_cache(maxsize=1)
def load_sources(path: Path = SOURCES_YAML) -> SourcesConfig:
    """Parse and validate sources.yaml. Raises at import time on a bad config."""
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))

    base = raw["base"]
    if not base["local_sync"]["enabled"]:
        raise NotImplementedError(
            "Only base.local_sync is implemented. SharePoint Graph API mode is declared "
            "in sources.yaml but has no loader."
        )
    base_path = Path(base["local_sync"]["base_path"]).expanduser()

    sources: Dict[str, SourceSpec] = {}
    for source_type, block in raw["sources"].items():
        block = dict(block)
        sources[source_type] = SourceSpec(
            source_type=source_type,
            source_dir=base_path / block.pop("source_folder"),
            input_dir=DATA_DIR / "input" / block.pop("input_subdir"),
            **block,
        )

    return SourcesConfig(
        base_path=base_path,
        validation=ValidationSpec(**raw.get("validation", {})),
        sources=sources,
    )
