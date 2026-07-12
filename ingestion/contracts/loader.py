"""
Source contract loader.

Contracts are declared in contracts.yaml (this directory) and loaded once
per process — the result is cached, since the schema is static for the
life of a pipeline run and there's no reason for every extractor
instantiation to re-read and re-parse the file.

Deliberately named contracts.yaml, and deliberately kept out of
ingestion/config/sources.yaml: that file handles file-discovery routing
(directories, file patterns, folders) for a *different* consumer
(FileDiscovery / load_sources_config), and giving schema its own file
under its own name removes any risk of the two colliding if someone ever
points this loader at the wrong path.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Dict, Set, Tuple

import yaml


class SourceContract:
    def __init__(self, name: str, required: Set[str], expected: Set[str]) -> None:
        self.name = name
        self.required = required
        self.expected = expected

    def validate(self, actual: Set[str]) -> Tuple[bool, Set[str], Set[str]]:
        """
        Returns (is_valid, missing, unexpected).

        is_valid is False iff any required column is absent. `unexpected`
        (present but outside `expected`) never affects is_valid — it's
        informational, for logging a schema-drift warning upstream.
        """
        missing = self.required - actual
        unexpected = actual - self.expected
        return (len(missing) == 0), missing, unexpected

    def __repr__(self) -> str:
        return f"SourceContract(name={self.name!r})"


@lru_cache(maxsize=1)
def load_contracts(yaml_path: Path | None = None) -> Dict[str, SourceContract]:
    if yaml_path is None:
        yaml_path = Path(__file__).parent / "contracts.yaml"

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    contracts: Dict[str, SourceContract] = {}
    for name, spec in data.get("sources", {}).items():
        contracts[name] = SourceContract(
            name=name,
            required=set(spec.get("required_columns", [])),
            expected=set(spec.get("expected_columns", [])),
        )
    return contracts
