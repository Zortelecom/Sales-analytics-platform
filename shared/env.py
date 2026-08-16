"""
.env loading.

WHY THIS EXISTS
───────────────
sqlmesh/config.yaml reads DUCKLAKE_CATALOG_PATH and PARQUET_PATH via
env_var(), and SQLMesh loads .env itself. Plain Python does not. So without
this, `python -m ingestion.main` reads the process environment (usually empty)
while SQLMesh reads .env — and the two layers silently point at different
lakes. Ingestion writes landing.* to one catalog, raw.* models look for it in
another, and nothing errors: the models just find no tables.

No dependency on python-dotenv. pydantic v2 dropped the [dotenv] extra, so
whether it is installed depends on how pyproject resolved — not something a
config path should be uncertain about. The parser below covers what a .env
actually contains.

override=False on purpose: a variable already set in the real environment
beats the file. That is what makes `DUCKLAKE_CATALOG_PATH=/tmp/fixture.ducklake
python -m ...` work for pointing a layer at a fixture without editing .env.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Dict

from shared.paths import PROJECT_ROOT

logger = logging.getLogger(__name__)

ENV_FILE = PROJECT_ROOT / ".env"


def parse_env_file(path: Path) -> Dict[str, str]:
    """Minimal .env parser: KEY=VALUE, # comments, optional quotes, optional `export`."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            logger.warning("%s:%d ignored, no '=': %s", path.name, lineno, raw)
            continue

        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()

        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]

        # A .env is not a shell. $(pwd) and ${VAR} are literal text here, and
        # silently storing them produces a path that exists nowhere.
        if "$(" in value or "${" in value or value.startswith("$"):
            logger.warning(
                "%s:%d %s contains shell syntax %r, which is NOT expanded in a "
                ".env file. Use an absolute path, or a path relative to the "
                "project root.", path.name, lineno, key, value,
            )

        values[key] = value
    return values


@lru_cache(maxsize=1)
def load_env(path: Path = ENV_FILE) -> Dict[str, str]:
    """Load .env into os.environ without overriding what is already set."""
    values = parse_env_file(path)
    applied = {}
    for key, value in values.items():
        if key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    if applied:
        logger.debug("Loaded %d variable(s) from %s", len(applied), path)
    return values


def resolve_path(value: str | None, default: Path) -> Path:
    """
    A path from the environment, resolved against PROJECT_ROOT when relative.

    Keeps .env portable: `PARQUET_PATH=data/warehouse/
    ` means the
    same thing regardless of which directory the command was run from, which is
    the same reason PROJECT_ROOT exists in shared/paths.py.
    """
    if not value:
        return default
    candidate = Path(value).expanduser()
    return candidate if candidate.is_absolute() else (PROJECT_ROOT / candidate)
