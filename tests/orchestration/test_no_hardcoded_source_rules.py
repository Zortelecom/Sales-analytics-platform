"""
Guards against processing rules drifting back into Python.

Layer: orchestration. sources.yaml and preprocessing.py had already diverged
once — the yaml said the sales delete pattern was "Synthese*" while the code
used "Synthese *", and nothing failed because SourceConfig.processing_rules was
loaded and never read. This test is what would have caught it.
"""
from __future__ import annotations

import re

import pytest

from shared.paths import PROJECT_ROOT

PREPROCESSING_PY = PROJECT_ROOT / "orchestration" / "assets" / "preprocessing.py"


def test_preprocessing_does_not_branch_on_source_type():
    if not PREPROCESSING_PY.exists():
        pytest.skip(f"{PREPROCESSING_PY} not found")

    offenders = [
        line.strip()
        for line in PREPROCESSING_PY.read_text(encoding="utf-8").splitlines()
        if re.search(r'source_type\s*==\s*["\']', line)
    ]
    assert not offenders, (
        "preprocessing.py branches on source_type; those rules belong in "
        "sources.yaml `processing:`:\n  " + "\n  ".join(offenders)
    )
