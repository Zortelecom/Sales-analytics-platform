"""
settings.py's adapter over the source registry.

Layer: ingestion. The four named path properties are back-compat shims for
FileDiscovery; adding a source must not require editing them.
"""
from __future__ import annotations

from pathlib import Path

from ingestion.config.settings import get_source, load_sources_config


def test_named_path_shims_resolve():
    config = load_sources_config()
    for attr in ("sales_path", "targets_path", "references_path", "kp_sd_path"):
        assert isinstance(getattr(config, attr), Path)


def test_paths_are_addressable_by_source_type():
    """The shims are legacy; this is how new code should reach a path."""
    config = load_sources_config()
    assert set(config.source_paths) == set(config.input_paths)
    assert config.source_paths["kp_sd"] == config.kp_sd_path


def test_processing_rules_cover_every_source():
    config = load_sources_config()
    for source_type, rules in config.processing_rules.items():
        assert set(rules) == {
            "delete_sheets_pattern", "required_sheets", "file_pattern"
        }, (
            f"{source_type}: unexpected processing keys {sorted(rules)}"
        )


def test_get_source_returns_a_spec():
    assert get_source("sales").input_dir.name == "sales"
