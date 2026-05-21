from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
SQLMESH_DIR = PROJECT_ROOT / "sqlmesh"
SQLMESH_SEEDS_DIR = SQLMESH_DIR / "seeds"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
ARCHIVE_DIR = DATA_DIR / "archive"

INPUT_SALES_DIR = DATA_DIR / "input" / "sales"
INPUT_TARGETS_DIR = DATA_DIR / "input" / "targets"
INPUT_REFERENCES_DIR = DATA_DIR / "input" / "references"

INPUT_PATHS = {
    "sales":      INPUT_SALES_DIR,
    "targets":    INPUT_TARGETS_DIR,
    "references": INPUT_REFERENCES_DIR,
}

DATA_ROOT = DATA_DIR
