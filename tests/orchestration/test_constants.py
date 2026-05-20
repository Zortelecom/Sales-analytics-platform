# tests/orchestration/test_constants.py
"""Tests for path resolution helpers in orchestration.utils.constants."""

from pathlib import Path
import pytest
from orchestration.utils.constants import get_serving_db_path, WAREHOUSE_DIR


def test_get_serving_db_path_prod():
    path = get_serving_db_path("prod")
    assert path.name == "serving.db"
    assert path.parent == WAREHOUSE_DIR


def test_get_serving_db_path_dev():
    path = get_serving_db_path("dev")
    assert path.name == "serving_dev.db"
    assert path.parent == WAREHOUSE_DIR


def test_get_serving_db_path_arbitrary_env():
    path = get_serving_db_path("staging")
    assert path.name == "serving_staging.db"
    assert path.parent == WAREHOUSE_DIR