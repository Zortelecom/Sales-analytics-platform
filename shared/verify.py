"""
shared/verify.py  —  does this machine satisfy the environment contract?

    python -m shared.verify              every section
    python -m shared.verify --section postgres catalog
    python -m shared.verify --json       machine-readable, for CI later

WHY THIS EXISTS
───────────────
Every layer already validates its own inputs, and none of them validates the
CONTRACT BETWEEN layers -- the handful of facts that must agree across .env,
PostgreSQL, the DuckLake catalog, SQLMesh and Dagster before anything runs.

A single afternoon produced four failures that all live in that gap:

    a folder rename broke the DuckLake catalog's stored data_path, so
    `sqlmesh plan` failed on seven staging models with an IO error naming a
    directory that no longer existed;

    the same rename left DAGSTER_HOME pointing at the old path, so
    `dagster dev` refused to start;

    `ALTER ROLE sqlmesh_svc SET "role" = 'lake_owner'` applied GLOBALLY rather
    than per-database, so SQLMesh's state connection ran as a role with no
    CREATE on its own schema -- surfacing as InFailedSqlTransaction, which
    names neither the role nor the permission;

    Dagster's SQLite storage fell over under the multiprocess executor and
    killed the scheduler daemon, silently, mid-run.

Each took much longer to diagnose than to fix, because each presented as
something else. Every one is a seconds-long check here.

WHAT THIS IS NOT
────────────────
Not a health check on the DATA -- that is meta.workbook_health and the
data-quality audits. This checks the plumbing: can each component reach what
it is configured to reach, and do the components agree with each other.

RULES THIS FILE FOLLOWS
───────────────────────
1. READ ONLY. No writes, no CREATE, no write locks. Safe to run while the
   pipeline is running. Every lake attach is read-only.
2. EVERY CHECK RUNS. A failing check never stops the ones after it -- the
   whole value is seeing all four problems in one pass rather than fixing one
   and rediscovering the next. Exceptions inside a check become a FAIL row.
3. EVERY FAILURE CARRIES A FIX. A check that tells you something is wrong
   without telling you what to do is a check you learn to skim.
4. NO IMPORT OF dagster OR sqlmesh. Both are slow to import and neither is
   needed: their configuration is files and databases, which we read directly.

EXIT CODES
    0  no failures (warnings are fine)
    1  at least one FAIL
    2  the verifier itself broke
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

# ── Status vocabulary ──────────────────────────────────────────────────────
# FAIL  the pipeline will not work, or will work wrongly
# WARN  works today, will bite later, or cannot be fully confirmed
# SKIP  not applicable to this configuration (e.g. Postgres checks on the
#       DuckDB file backend) -- distinct from OK, because "not checked" and
#       "checked and fine" must never look the same
OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"

_GLYPH = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]", SKIP: "[skip]"}


@dataclass
class Result:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


@dataclass
class Section:
    name: str
    results: List[Result] = field(default_factory=list)

    def add(self, name: str, status: str, detail: str = "", fix: str = "") -> None:
        self.results.append(Result(name, status, detail, fix))


# ═══════════════════════════════════════════════════════════════════════════
# .env file — parsed as TEXT, not through the loader
#
# Deliberate: the loader is what we are trying to catch out. An inline comment
# that python-dotenv happens to strip today is still a latent failure for any
# other reader of the same file -- shared/lake.py does int(PG_CATALOG_PORT),
# and a stray comment there raises a bare ValueError from a function whose
# name mentions secrets and whose message mentions neither the file nor the
# variable.
# ═══════════════════════════════════════════════════════════════════════════

_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def scan_env_text(text: str) -> dict:
    """
    Parse .env as text and report structural problems.

    Returns {"vars": {name: raw_value}, "shell": [...], "inline": [...],
             "dupes": [...]}.

    Pure and side-effect free so it can be unit tested without a filesystem.
    """
    variables: dict = {}
    shell: List[str] = []
    inline: List[str] = []
    dupes: List[str] = []

    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ASSIGNMENT.match(line)
        if not match:
            continue

        name, raw = match.group(1), match.group(2).strip()

        if name in variables:
            dupes.append(f"line {lineno}: {name} assigned again (last wins)")
        variables[name] = raw

        # A .env is not a shell. $(pwd) and ${VAR} are literal text, and
        # ingestion.main --check already rejects them for two variables -- but
        # only two.
        if "$(" in raw or "${" in raw:
            shell.append(f"line {lineno}: {name} contains shell syntax")

        # Inline comment on an UNQUOTED value. Quoted values are unambiguous,
        # so only flag the bare form.
        if raw and raw[0] not in "\"'" and re.search(r"\s#", raw):
            inline.append(f"line {lineno}: {name} has a trailing # comment")

    return {"vars": variables, "shell": shell, "inline": inline, "dupes": dupes}


def check_env_file(project_root: Path) -> Section:
    section = Section("Environment file")
    env_path = project_root / ".env"

    if not env_path.exists():
        section.add(
            ".env present", FAIL,
            f"not found at {env_path}",
            "cp .env.example .env, then fill in the PG_CATALOG_* values",
        )
        return section

    section.add(".env present", OK, str(env_path))

    scan = scan_env_text(env_path.read_text(encoding="utf-8", errors="replace"))

    section.add(
        "no shell syntax",
        FAIL if scan["shell"] else OK,
        "; ".join(scan["shell"]) or "none",
        "a .env is not a shell -- write the literal path instead of $(pwd)",
    )

    # WARN not FAIL: python-dotenv strips these for unquoted values, so the
    # pipeline works today. It is the next reader that breaks.
    section.add(
        "no inline comments",
        WARN if scan["inline"] else OK,
        "; ".join(scan["inline"]) or "none",
        "move the comment to its own line -- not every consumer strips it",
    )

    section.add(
        "no duplicate assignments",
        WARN if scan["dupes"] else OK,
        "; ".join(scan["dupes"]) or "none",
        "delete the earlier assignment; a silent last-wins is hard to spot",
    )

    # Presence only. Whether the credentials WORK is the postgres section.
    required = ["PARQUET_PATH"]
    missing = [v for v in required if not os.getenv(v)]
    section.add(
        "required variables set",
        FAIL if missing else OK,
        f"missing: {missing}" if missing else "PARQUET_PATH",
        "these are read by ingestion, SQLMesh and serving alike",
    )
    return section


# ═══════════════════════════════════════════════════════════════════════════
# Paths — where the rename damage shows up first
# ═══════════════════════════════════════════════════════════════════════════

def check_paths(project_root: Path) -> Section:
    section = Section("Paths")

    # THE RENAME CHECK. .env carries an absolute PROJECT_ROOT; shared.paths
    # derives one from this file's location. When the project folder is
    # renamed or copied, they diverge -- and nothing else in the platform
    # compares them, so the mismatch surfaces layers away as a missing file.
    declared = os.getenv("PROJECT_ROOT")
    if not declared:
        section.add(
            "PROJECT_ROOT agrees with disk", SKIP,
            "PROJECT_ROOT not set in .env; shared.paths derives it",
        )
    else:
        same = Path(declared).resolve() == project_root.resolve()
        section.add(
            "PROJECT_ROOT agrees with disk",
            OK if same else FAIL,
            f"declared {declared!r} vs actual {project_root}",
            "the project was moved or renamed -- update PROJECT_ROOT, "
            "DATA_DIR, WAREHOUSE_DIR and PARQUET_PATH in .env, and expect the "
            "DuckLake data_path and DAGSTER_HOME to need the same treatment",
        )

    for var in ("DATA_DIR", "WAREHOUSE_DIR", "PARQUET_PATH"):
        raw = os.getenv(var)
        if not raw:
            section.add(f"{var} exists", SKIP, "not set; a default will be used")
            continue
        path = Path(raw)
        if path.exists():
            section.add(f"{var} exists", OK, str(path))
        else:
            # PARQUET_PATH is created on first write, so absence is only a
            # warning -- but combined with a PROJECT_ROOT mismatch above it is
            # the signature of a move.
            section.add(
                f"{var} exists", WARN, f"{path} does not exist yet",
                "created on first write; suspicious if the pipeline has run",
            )
    return section


# ═══════════════════════════════════════════════════════════════════════════
# PostgreSQL — one connection per configured role
# ═══════════════════════════════════════════════════════════════════════════

def _pg_connect(dbname: str, user: str, password: str, host: str, port: str):
    import psycopg2  # imported lazily: absent on a DuckDB-file-only install
    return psycopg2.connect(
        dbname=dbname, user=user, password=password,
        host=host, port=port, connect_timeout=5,
    )


def check_postgres(section_name: str = "PostgreSQL roles") -> Section:
    section = Section(section_name)

    host = os.getenv("PG_CATALOG_HOST")
    if not host:
        section.add(
            "backend", SKIP,
            "PG_CATALOG_HOST unset -- catalog is the DuckDB file backend",
        )
        return section

    port = os.getenv("PG_CATALOG_PORT", "5432")
    database = os.getenv("PG_CATALOG_DB", "sales_lakehouse")
    section.add("backend", OK, f"postgres {host}:{port}/{database}")

    # Port must be an integer: shared/lake.py does int(settings["port"]) when
    # building the DuckDB secret, and raises ValueError -- not duckdb.Error --
    # so it escapes that function's own exception handler.
    try:
        int(port)
        section.add("PG_CATALOG_PORT numeric", OK, port)
    except ValueError:
        section.add(
            "PG_CATALOG_PORT numeric", FAIL, repr(port),
            "lake._create_secret does int() on this and the ValueError "
            "escapes its handler -- probably a trailing inline comment",
        )

    # Every role the platform asks for by name. shared/lake.py falls back to
    # PG_CATALOG_USER when a per-role variable is unset, which is correct but
    # means least privilege silently is not in force -- worth saying out loud.
    roles = [
        (None, "default (ingestion)"),
        ("reader", "marts_validation, sensors, Streamlit"),
        ("writer", "data_quality_report"),
        ("publisher", "serving/publish.py"),
    ]

    default_user = os.getenv("PG_CATALOG_USER")
    default_password = os.getenv("PG_CATALOG_PASSWORD", "")

    for role, used_by in roles:
        suffix = f"_{role.upper()}" if role else ""
        user = os.getenv(f"PG_CATALOG_USER{suffix}") or default_user
        password = os.getenv(f"PG_CATALOG_PASSWORD{suffix}") or default_password
        label = f"connect as {role or 'default'}"

        if not user:
            section.add(label, FAIL, "no user resolved", "set PG_CATALOG_USER")
            continue

        distinct = bool(os.getenv(f"PG_CATALOG_USER{suffix}")) or role is None

        try:
            conn = _pg_connect(database, user, password, host, port)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT current_user, session_user")
                    current, session = cur.fetchone()
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001
            section.add(
                label, FAIL, f"{user}: {type(exc).__name__}: {exc}".strip()[:200],
                f"used by {used_by}; check the role exists and has CONNECT",
            )
            continue

        if not distinct:
            section.add(
                label, WARN,
                f"falls back to {user} (no PG_CATALOG_USER{suffix})",
                f"{used_by} runs with the default credential -- least "
                f"privilege is configured but not requested",
            )
        elif current != session:
            # A SET ROLE is in force. Fine in the catalog, where it is how
            # ownership is kept on lake_owner; the point is that it is VISIBLE.
            section.add(
                label, OK, f"{session} -> SET ROLE {current}",
            )
        else:
            section.add(label, OK, f"{user}")

    return section


def check_sqlmesh_state() -> Section:
    """
    SQLMesh's state connection is a different database and a different role
    from the catalog, and that difference is exactly where it broke.
    """
    section = Section("SQLMesh state")

    user = os.getenv("SQLMESH_STATE_USER")
    database = os.getenv("SQLMESH_STATE_DB")
    if not (user and database):
        section.add("configured", SKIP, "SQLMESH_STATE_USER/DB unset")
        return section

    host = os.getenv("PG_CATALOG_HOST", "localhost")
    port = os.getenv("PG_CATALOG_PORT", "5432")
    password = os.getenv("SQLMESH_STATE_PASSWORD", "")

    try:
        conn = _pg_connect(database, user, password, host, port)
    except Exception as exc:  # noqa: BLE001
        section.add(
            "connect", FAIL, f"{user}@{database}: {exc}".strip()[:200],
            "check the role and database exist and CONNECT is granted",
        )
        return section

    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_user, session_user, current_schema()")
            current, session, schema = cur.fetchone()
            section.add("connect", OK, f"{user}@{database}")

            # THE ONE THAT COST AN AFTERNOON.
            #
            # `ALTER ROLE sqlmesh_svc SET "role" = 'lake_owner'` with no IN
            # DATABASE clause applies to EVERY database. In sqlmesh_state,
            # lake_owner owns nothing and is granted nothing, so the very first
            # CREATE TABLE of the state migration fails -- and SQLMesh's
            # rollback handler then queries the aborted transaction, so the
            # error you actually see is InFailedSqlTransaction, which names
            # neither the role nor the permission.
            if current != session:
                section.add(
                    "no SET ROLE leak", FAIL,
                    f"session_user={session} but current_user={current}",
                    f'ALTER ROLE {session} RESET "role"; then re-apply it '
                    f'scoped: ALTER ROLE {session} IN DATABASE '
                    f'<catalog> SET "role" = \'<owner>\'',
                )
            else:
                section.add("no SET ROLE leak", OK, f"running as {current}")

            # The actual capability the migration needs. Cheaper and more
            # honest than inspecting ACLs: ask PostgreSQL directly.
            cur.execute(
                "SELECT has_schema_privilege(current_user, current_schema(), 'CREATE'), "
                "       has_schema_privilege(current_user, current_schema(), 'USAGE')"
            )
            can_create, can_use = cur.fetchone()
            section.add(
                f"CREATE on schema {schema}",
                OK if (can_create and can_use) else FAIL,
                f"create={can_create} usage={can_use}",
                f"GRANT USAGE, CREATE ON SCHEMA {schema} TO {current}; "
                f"PostgreSQL 15+ no longer grants CREATE on public to PUBLIC",
            )

            # State tables are created on first plan; absence is normal before
            # then, so this reports rather than judges.
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name LIKE %s", ("%_snapshots%",),
            )
            (snapshots,) = cur.fetchone()
            section.add(
                "state initialised",
                OK if snapshots else WARN,
                f"{snapshots} snapshot table(s)",
                "run `sqlmesh plan dev` once to initialise state",
            )
    except Exception as exc:  # noqa: BLE001
        section.add("state inspection", FAIL, f"{type(exc).__name__}: {exc}"[:200])
    finally:
        conn.close()

    return section


# ═══════════════════════════════════════════════════════════════════════════
# DuckLake catalog — the stored data_path is the rename's real victim
# ═══════════════════════════════════════════════════════════════════════════

def check_catalog() -> Section:
    section = Section("DuckLake catalog")

    try:
        from shared import lake
    except Exception as exc:  # noqa: BLE001
        section.add("import shared.lake", FAIL, str(exc)[:200])
        return section

    try:
        section.add("configured source", OK, lake.describe())
    except Exception as exc:  # noqa: BLE001
        # describe() calls _pg_settings, which RAISES when PG_CATALOG_HOST is
        # set and PG_CATALOG_USER is not -- from a function documented as
        # log-safe. Worth surfacing as its own row.
        section.add(
            "configured source", FAIL, f"{type(exc).__name__}: {exc}"[:200],
            "lake.describe() should never raise; the configuration is "
            "half-set",
        )

    # Read-only attach, so this is safe mid-pipeline.
    try:
        con = lake.connect(read_only=True, role="reader")
    except Exception as exc:  # noqa: BLE001
        section.add(
            "attach (read-only)", FAIL, f"{type(exc).__name__}: {exc}"[:300],
            "if this mentions DATA_PATH, the catalog's stored path is stale",
        )
        return section

    try:
        section.add("attach (read-only)", OK, f"alias {lake.CATALOG_ALIAS}")

        schemas = {
            r[0] for r in con.execute(
                "SELECT schema_name FROM duckdb_schemas() WHERE database_name = ?",
                [lake.CATALOG_ALIAS],
            ).fetchall()
        }
        section.add(
            "landing schema present",
            OK if "landing" in schemas else FAIL,
            f"schemas: {sorted(s for s in schemas if not s.startswith('information'))}",
            "run ingestion once: python -m ingestion.main --all",
        )

        if "landing" in schemas:
            try:
                (files,) = con.execute(
                    f'SELECT count(*) FROM "{lake.CATALOG_ALIAS}".landing.file_registry '
                    f"WHERE status = 'ingested'"
                ).fetchone()
                section.add(
                    "files ingested",
                    OK if files else WARN,
                    f"{files} file(s) registered as ingested",
                    "landing is empty -- ingestion has not run successfully",
                )
            except Exception as exc:  # noqa: BLE001
                section.add("files ingested", WARN, str(exc)[:200])
    finally:
        con.close()

    section.results.extend(_catalog_data_path().results)
    return section


def _catalog_data_path() -> Section:
    """
    Compare the data_path STORED IN THE CATALOG against PARQUET_PATH.

    DuckLake records this at catalog-creation time. Rename the project folder
    and the files move while the pointer does not -- so ingestion happily
    CREATES the old directory again on write (DuckDB makes directories), while
    SQLMesh fails reading files that were never there. That is what produced

        IO Error: Cannot open file "...v0.4\\data\\warehouse\\parquet\\..."

    on seven staging models while landing_load had just reported success.

    Best-effort: the metadata table and column names are DuckLake's own and
    may change between versions, so an inspection failure is a WARN with the
    reason attached, never a crash.
    """
    section = Section("")
    host = os.getenv("PG_CATALOG_HOST")
    if not host:
        section.add("stored data_path", SKIP, "DuckDB file backend")
        return section

    configured = os.getenv("PARQUET_PATH")
    try:
        conn = _pg_connect(
            os.getenv("PG_CATALOG_DB", "sales_lakehouse"),
            os.getenv("PG_CATALOG_USER", ""),
            os.getenv("PG_CATALOG_PASSWORD", ""),
            host, os.getenv("PG_CATALOG_PORT", "5432"),
        )
    except Exception as exc:  # noqa: BLE001
        section.add("stored data_path", WARN, f"cannot inspect: {exc}"[:200])
        return section

    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT value FROM ducklake_metadata WHERE key = 'data_path'"
            )
            row = cur.fetchone()
            stored = row[0] if row else None

            if stored is None:
                section.add("stored data_path", WARN, "no data_path row found")
            elif configured is None:
                section.add("stored data_path", WARN, f"stored {stored!r}, "
                                                      "PARQUET_PATH unset")
            else:
                # Compare as normalised paths: the stored form carries a
                # trailing slash and forward slashes on Windows.
                same = (
                    Path(stored.rstrip("/\\")).resolve()
                    == Path(configured.rstrip("/\\")).resolve()
                )
                section.add(
                    "stored data_path matches PARQUET_PATH",
                    OK if same else FAIL,
                    f"catalog {stored!r} vs env {configured!r}",
                    "UPDATE ducklake_metadata SET value = '<new path>/' WHERE "
                    "key = 'data_path'; and move any files written under the "
                    "old path before re-running",
                )

            # Tells you whether the NEXT rename will hurt.
            try:
                cur.execute(
                    "SELECT bool_and(path_is_relative) FROM ducklake_data_file"
                )
                (relative,) = cur.fetchone()
                if relative is None:
                    section.add("data file paths", WARN, "no data files yet")
                else:
                    section.add(
                        "data file paths relative",
                        OK if relative else WARN,
                        "relative" if relative else "absolute",
                        "absolute per-file paths mean a future move needs every "
                        "row rewritten, not just data_path",
                    )
            except Exception as exc:  # noqa: BLE001
                section.add("data file paths", WARN, str(exc)[:150])
    except Exception as exc:  # noqa: BLE001
        section.add(
            "stored data_path", WARN,
            f"could not read ducklake_metadata: {exc}"[:200],
            "DuckLake may have renamed its metadata tables in this version",
        )
    finally:
        conn.close()

    return section


# ═══════════════════════════════════════════════════════════════════════════
# SQLMesh project files
# ═══════════════════════════════════════════════════════════════════════════

def check_sqlmesh_project(project_root: Path) -> Section:
    section = Section("SQLMesh project")
    root = project_root / "sqlmesh"

    if not root.is_dir():
        section.add(
            "project directory", FAIL, f"{root} not found",
            "SQLMeshResource resolves project_path against PROJECT_ROOT",
        )
        return section
    section.add("project directory", OK, str(root))

    config = root / "config.yaml"
    section.add(
        "config.yaml", OK if config.exists() else FAIL, str(config),
        "SQLMesh cannot run without it",
    )

    # Without this, raw.* still build but column-level lineage stops at the
    # landing boundary and plan cannot warn about a column staging expects and
    # landing no longer has.
    external = root / "external_models.yaml"
    if not external.exists():
        section.add(
            "external_models.yaml", WARN, "absent",
            "cd sqlmesh && sqlmesh create_external_models",
        )
    else:
        size = external.stat().st_size
        section.add(
            "external_models.yaml",
            OK if size > 0 else WARN,
            f"{size} bytes",
            "regenerate after a landing schema change",
        )
    return section


# ═══════════════════════════════════════════════════════════════════════════
# Dagster
# ═══════════════════════════════════════════════════════════════════════════

def check_dagster(project_root: Path) -> Section:
    section = Section("Dagster")

    home = os.getenv("DAGSTER_HOME")
    if not home:
        section.add(
            "DAGSTER_HOME set", FAIL, "unset",
            "without it, dagster dev uses a TEMP directory and discards run "
            "history AND sensor cursors on every restart -- so "
            "pipeline_health_sensor's column fingerprints never survive to be "
            "compared, and drift detection can never fire",
        )
        return section

    home_path = Path(home)
    if not home_path.is_dir():
        section.add(
            "DAGSTER_HOME exists", FAIL, f"{home} is not a directory",
            "Dagster does not create it: mkdir it, or fix the path after a "
            "project rename",
        )
        return section
    section.add("DAGSTER_HOME exists", OK, str(home_path))

    instance_yaml = home_path / "dagster.yaml"
    if not instance_yaml.exists():
        section.add(
            "dagster.yaml", WARN, "absent -- defaults to SQLite storage",
            "SQLite storage failed under the multiprocess executor and killed "
            "the scheduler daemon mid-run; configure PostgreSQL storage",
        )
        return section

    text = instance_yaml.read_text(encoding="utf-8", errors="replace")
    # A substring test, not a YAML parse: we only need to know which backend,
    # and this avoids a hard dependency on pyyaml for one question.
    if "postgres" in text:
        section.add("run/event storage", OK, "PostgreSQL configured")
    else:
        section.add(
            "run/event storage", WARN, "SQLite (default)",
            "ten step subprocesses against one SQLite file produced "
            "'disk I/O error' and left the scheduler daemon dead without "
            "failing the run -- move storage to PostgreSQL",
        )

    # pyproject discovery: `module_name` is the recognised key. `module` is
    # silently ignored, which presents as a code location that loads nothing
    # and an empty asset graph rather than as an error.
    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text(encoding="utf-8", errors="replace")
        if "[tool.dagster]" not in content:
            section.add(
                "pyproject [tool.dagster]", WARN, "absent",
                "add module_name = \"orchestration.definitions\" so plain "
                "`dagster dev` finds the code location",
            )
        elif re.search(r"^\s*module_name\s*=", content, re.MULTILINE):
            section.add("pyproject [tool.dagster]", OK, "module_name set")
        else:
            section.add(
                "pyproject [tool.dagster]", FAIL,
                "[tool.dagster] present but module_name is not",
                "the key is `module_name`; `module` is silently ignored and "
                "yields an empty asset graph with no error",
            )
    return section


# ═══════════════════════════════════════════════════════════════════════════
# Runner
# ═══════════════════════════════════════════════════════════════════════════

SECTIONS: dict = {}


def _register(key: str, fn: Callable[[], Section]) -> None:
    SECTIONS[key] = fn


def run(selected: Optional[List[str]] = None) -> List[Section]:
    """
    Run each section, isolating failures.

    A section that raises becomes a FAIL row rather than aborting the run --
    the entire premise is that you see every problem in one pass.
    """
    out: List[Section] = []
    for key, fn in SECTIONS.items():
        if selected and key not in selected:
            continue
        try:
            out.append(fn())
        except Exception as exc:  # noqa: BLE001
            section = Section(key)
            section.add(
                "section crashed", FAIL, f"{type(exc).__name__}: {exc}"[:300],
                "this is a bug in shared/verify.py, not necessarily in your "
                "environment",
            )
            out.append(section)
    return out


def render(sections: List[Section], show_fixes: bool = True) -> str:
    lines: List[str] = []
    for section in sections:
        if not section.results:
            continue
        if section.name:
            lines.append("")
            lines.append(section.name)
            lines.append("-" * max(len(section.name), 3))
        for r in section.results:
            lines.append(f"  {_GLYPH[r.status]}  {r.name}"
                         + (f"   {r.detail}" if r.detail else ""))
            if show_fixes and r.status in (FAIL, WARN) and r.fix:
                for wrapped in _wrap(r.fix, 74):
                    lines.append(f"           -> {wrapped}")
    return "\n".join(lines)


def _wrap(text: str, width: int) -> List[str]:
    words, line, out = text.split(), "", []
    for word in words:
        if len(line) + len(word) + 1 > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the environment contract across every layer.",
    )
    parser.add_argument("--section", nargs="*", choices=sorted(SECTIONS),
                        help="Run only these sections. Default: all.")
    parser.add_argument("--json", action="store_true",
                        help="Machine-readable output.")
    parser.add_argument("--quiet", action="store_true",
                        help="Suppress the fix hints.")
    args = parser.parse_args(argv)

    try:
        from shared.env import load_env
        load_env()
    except Exception as exc:  # noqa: BLE001
        print(f"Could not load .env via shared.env: {exc}", file=sys.stderr)

    sections = run(args.section)

    counts = {OK: 0, WARN: 0, FAIL: 0, SKIP: 0}
    for section in sections:
        for r in section.results:
            counts[r.status] += 1

    if args.json:
        print(json.dumps({
            "summary": counts,
            "sections": [
                {"name": s.name,
                 "results": [vars(r) for r in s.results]}
                for s in sections
            ],
        }, indent=2))
    else:
        print(render(sections, show_fixes=not args.quiet))
        print()
        print(f"  {counts[OK]} ok, {counts[WARN]} warning(s), "
              f"{counts[FAIL]} failure(s), {counts[SKIP]} skipped")
        if counts[FAIL]:
            print("\n  Fix the failures above before running the pipeline.")

    return 1 if counts[FAIL] else 0


def _project_root() -> Path:
    try:
        from shared.paths import PROJECT_ROOT
        return Path(PROJECT_ROOT)
    except Exception:  # noqa: BLE001
        return Path(__file__).resolve().parent.parent


_register("env", lambda: check_env_file(_project_root()))
_register("paths", lambda: check_paths(_project_root()))
_register("postgres", check_postgres)
_register("state", check_sqlmesh_state)
_register("catalog", check_catalog)
_register("sqlmesh", lambda: check_sqlmesh_project(_project_root()))
_register("dagster", lambda: check_dagster(_project_root()))


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as exc:  # noqa: BLE001
        print(f"verify itself failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(2)
