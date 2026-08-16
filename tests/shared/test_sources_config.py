"""
Source registry parsing.

Layer: shared. Imports only shared.* — no ingestion, no orchestration, no
Dagster. If this file ever needs one of those, the dependency is pointing the
wrong way and shared/ has stopped being shared.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from shared.paths import DATA_DIR
from shared.sources import load_sources


def test_config_parses():
    """A malformed sources.yaml must fail here, not three assets into a run."""
    assert load_sources().sources, "no sources declared"


def test_input_dirs_are_under_data_input():
    for source_type, spec in load_sources().sources.items():
        assert spec.input_dir.parent == DATA_DIR / "input", (
            f"{source_type}: input_dir {spec.input_dir} is outside data/input"
        )


def test_input_dirs_are_unique():
    """Two sources sharing an input dir means one extractor eats the other's files."""
    dirs = [s.input_dir for s in load_sources().sources.values()]
    assert len(dirs) == len(set(dirs)), f"duplicate input_dir: {dirs}"


def test_source_dirs_share_the_declared_base():
    registry = load_sources()
    for source_type, spec in registry.sources.items():
        assert registry.base_path in spec.source_dir.parents, (
            f"{source_type}: source_dir {spec.source_dir} escapes base_path"
        )


@pytest.mark.parametrize("source_type", ["sales", "targets", "references", "kp_sd"])
def test_known_sources_still_declared(source_type):
    """Guards against a rename silently orphaning an extractor."""
    assert source_type in load_sources()


def test_every_source_names_a_contract():
    """
    Column contracts live in contracts.yaml. Declaring them in sources.yaml too
    is how is_stockout survived there long after the column was renamed.
    """
    for source_type, spec in load_sources().sources.items():
        assert spec.contract, f"{source_type} names no contract"


def test_unknown_source_type_gives_a_useful_error():
    with pytest.raises(KeyError, match="Declared sources"):
        load_sources()["kp_sd_destocke"]
