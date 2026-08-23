"""
orchestration/resources/sqlmesh_resource.py

(2026-08) Rewritten against the VERIFIED CLI surface. This code had never run
under Dagster -- SQLMesh had only been driven from the terminal -- so every
flag in the previous version was an assumption. Two were wrong.

    sqlmesh audit  ->  --model, --start/-s, --end/-e, --execution-time
    sqlmesh info   ->  --skip-connection, --verbose/-v

CONSEQUENCE 1: `audit` HAS NO ENVIRONMENT OPTION
────────────────────────────────────────────────
The previous version passed `--environment {env}`, which the CLI rejects at
argument parsing, exiting non-zero. _run_command raised Failure,
transformation.py caught it, and recorded audit_status="error" -- so a flag
that does not exist and a genuinely failing audit produced identical asset
metadata, and neither stopped the pipeline.

Removing the flag is not sufficient. A bare `sqlmesh audit` targets SQLMesh's
DEFAULT TARGET ENVIRONMENT, which is `prod` unless configured. Only `dev` has
ever been built here, so the corrected command audits an environment that does
not exist.

There is no CLI flag for this. Environment is positional on `plan` and `run`
and absent from `audit`. So:

  * SQLMESH__DEFAULT_TARGET_ENVIRONMENT is exported below. SQLMesh reads
    SQLMESH__-prefixed variables as config overrides. VERIFY THIS TAKES:
    run the pipeline once and confirm the audit output names dev, not prod.
    If it does not take, set `default_target_environment: dev` in
    sqlmesh/config.yaml instead -- global, so it also applies to your terminal
    runs, which is arguably what you want on a single-developer project.

  * The call is NON-FATAL. If the override silently no-ops, you get a logged
    warning and a recorded audit_status, not a dead asset.

  * `sqlmesh plan dev` REMAINS THE REAL GATE. Plan evaluates audits for every
    model it builds, correctly targeted, and a blocking audit fails the plan --
    which fails plan() (still fatal) and therefore the asset. This standalone
    call is a reporting convenience layered on top of that, not the enforcement
    point. If the environment override proves unreliable, deleting the call
    costs you metadata, not safety.

CONSEQUENCE 2: `info` HAS NO --format json
──────────────────────────────────────────
get_model_info() ran `sqlmesh info --format json` and swallowed the non-zero
exit as {}, so models_count has been 0 on every run with no trace of why.

`info` also runs a data-warehouse CONNECTION TEST by default -- an extra open
of the catalog in the middle of the pipeline, which on the DuckDB file backend
is an extra chance to collide with a writer. --skip-connection avoids it, and
the model count is in the text output regardless.

PATHS ARE ANCHORED ON PROJECT_ROOT
──────────────────────────────────
cwd was `self.project_path` ("sqlmesh", relative) and PYTHONPATH was
os.getcwd(). Both resolve against the DAEMON's working directory. Launching
from the project root works; `dagster dev --working-directory orchestration`
(as the README documents) would make cwd orchestration/sqlmesh, which does not
exist. A resource should not depend on how the daemon was started.
"""
import os
import re
import subprocess
from typing import List, Optional

from dagster import AssetExecutionContext, ConfigurableResource, Failure, MetadataValue
from pydantic import Field

from shared.paths import PROJECT_ROOT

# `sqlmesh info` prints a line like "Models: 46". Brittle by nature -- it is
# human-readable output, not an interface -- so a miss returns None rather than
# raising, and the raw text is attached to the asset either way.
_MODEL_COUNT = re.compile(r"^\s*models?\s*:\s*(\d+)", re.IGNORECASE | re.MULTILINE)
_MACRO_COUNT = re.compile(r"^\s*macros?\s*:\s*(\d+)", re.IGNORECASE | re.MULTILINE)


class SQLMeshResource(ConfigurableResource):
    """Resource for running SQLMesh commands."""

    project_path: str = Field(
        default="sqlmesh",
        description=(
            "SQLMesh project directory. Relative values resolve against "
            "PROJECT_ROOT, not the daemon's working directory."
        ),
    )
    environment: str = Field(
        default="dev",
        description="SQLMesh environment to target",
    )
    start_date: Optional[str] = Field(
        # Matches PipelineConfig.sqlmesh_start_date. These two defaults used to
        # disagree (2025-01-01 here, 2024-10-01 there); definitions.py passes
        # the config value so it was inert, but a second construction site
        # would have silently dropped three months of intervals.
        default="2024-10-01",
        description="Start date for incremental models (YYYY-MM-DD).",
    )
    run_audits: bool = Field(
        default=True,
        description=(
            "Run `sqlmesh audit` after planning. Purely for reporting -- plan "
            "already enforces audits on the models it builds. Set False if the "
            "environment override proves unreliable and the noise is not worth "
            "it."
        ),
    )

    def _cwd(self) -> str:
        """Absolute project directory, independent of how Dagster was launched."""
        path = os.path.expanduser(self.project_path)
        if not os.path.isabs(path):
            path = str(PROJECT_ROOT / path)
        if not os.path.isdir(path):
            raise Failure(
                description=(
                    f"SQLMesh project directory not found: {path}. "
                    f"project_path={self.project_path!r} resolved against "
                    f"PROJECT_ROOT={PROJECT_ROOT}."
                )
            )
        return path

    def _env(self) -> dict:
        """
        Subprocess environment.

        PYTHONPATH is PROJECT_ROOT so the SQLMesh process can import `shared`
        (config.yaml and the model macros reach into it). It used to be
        os.getcwd(), which is only correct when the daemon happens to be
        started from the root.

        SQLMESH__DEFAULT_TARGET_ENVIRONMENT is the only lever available for
        pointing `sqlmesh audit` at the environment we just planned -- see the
        module docstring. Harmless if unsupported.

        PYTHONIOENCODING is load-bearing on Windows: SQLMesh prints a checkmark
        in its audit summary, the console default is cp1252, and capturing it
        raised 'charmap' codec can't encode character '\\u2705' AFTER the audits
        had already run -- a perfectly good audit pass reported as an error
        because of one character in the output.
        """
        existing = os.environ.get("PYTHONPATH", "")
        root = str(PROJECT_ROOT)
        return {
            **os.environ,
            "PYTHONPATH": f"{root}{os.pathsep}{existing}" if existing else root,
            "PYTHONIOENCODING": "utf-8",
            "SQLMESH__DEFAULT_TARGET_ENVIRONMENT": self.environment,
        }

    def _run_command(
        self,
        cmd: List[str],
        context: Optional[AssetExecutionContext] = None,
        fatal: bool = True,
    ) -> subprocess.CompletedProcess:
        """
        Execute a SQLMesh CLI command.

        fatal=False returns the CompletedProcess on a non-zero exit instead of
        raising, for commands whose failure is a RESULT rather than an error.
        """
        full_cmd = ["sqlmesh"] + cmd

        if context:
            context.log.info("Running: %s", " ".join(full_cmd))

        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._cwd(),
            env=self._env(),
            check=False,
        )

        if result.returncode != 0 and fatal:
            if context:
                context.log.error(
                    "SQLMesh command failed (exit %s)\nSTDOUT: %s\nSTDERR: %s",
                    result.returncode, result.stdout, result.stderr,
                )
            raise Failure(
                description=f"SQLMesh {cmd[0]} failed: "
                            f"{result.stderr or result.stdout}"
            )

        if context and result.stdout:
            warning_lines = [
                line.strip()
                for line in result.stdout.splitlines()
                if "WARNING" in line.upper() or "WARN" in line.upper()
            ]
            if warning_lines:
                context.log.warning(
                    "SQLMesh emitted %d warning(s); attaching to asset metadata",
                    len(warning_lines),
                )
                context.add_output_metadata({
                    "sqlmesh_warnings": MetadataValue.text("\n".join(warning_lines))
                })

        return result

    def plan(
        self,
        context: AssetExecutionContext,
        auto_apply: bool = True,
        start_date: Optional[str] = None,
    ) -> subprocess.CompletedProcess:
        """
        Run `sqlmesh plan <environment>`. Environment is POSITIONAL.

        Fatal on failure, deliberately: this is where audits are actually
        enforced. A blocking audit fails the plan, which must fail the asset.
        """
        cmd = ["plan", self.environment]

        effective_start = start_date or self.start_date
        if effective_start:
            cmd.extend(["--start", effective_start])
            if context:
                context.log.info("SQLMesh plan will start from: %s", effective_start)

        if auto_apply:
            cmd.append("--auto-apply")

        result = self._run_command(cmd, context)
        context.log.info("SQLMesh plan output:\n%s", result.stdout)
        return result

    def run_transformations(
        self, context: AssetExecutionContext, select: Optional[str] = None
    ) -> subprocess.CompletedProcess:
        """
        Run `sqlmesh run <environment>`.

        Not called by any asset today: sqlmesh_models uses plan --auto-apply,
        which both applies changes and runs missing intervals. Kept for a
        future asset that wants to advance intervals without planning.
        """
        cmd = ["run", self.environment]
        if select:
            cmd.extend(["--select", select])
        result = self._run_command(cmd, context)
        context.log.info("SQLMesh run completed")
        return result

    def audit(
        self, context: AssetExecutionContext, start_date: Optional[str] = None
    ) -> Optional[subprocess.CompletedProcess]:
        """
        Run the audits for reporting. Non-fatal; returns None when disabled.

        NO --environment: the CLI has no such option. Targeting depends on
        SQLMESH__DEFAULT_TARGET_ENVIRONMENT taking effect -- confirm from the
        output on your first run that it names dev.

        Non-fatal because a failing audit is a data verdict for the caller to
        classify, not a subprocess error. Raising here made
        transformation.py's `"passed" if returncode == 0 else "failed"` branch
        unreachable: every real audit failure arrived in its except block as
        audit_status="error", alongside every genuine crash.
        """
        if not self.run_audits:
            context.log.info(
                "run_audits=False; skipping. Plan already enforced audits on "
                "the models it built."
            )
            return None

        cmd = ["audit"]
        effective_start = start_date or self.start_date
        if effective_start:
            cmd.extend(["--start", effective_start])

        result = self._run_command(cmd, context, fatal=False)

        if result.returncode == 0:
            context.log.info("SQLMesh audits passed")
        else:
            context.log.warning(
                "SQLMesh audits returned %s. If this mentions an environment "
                "you did not build, SQLMESH__DEFAULT_TARGET_ENVIRONMENT is not "
                "taking effect -- set default_target_environment in "
                "sqlmesh/config.yaml instead.\nSTDOUT: %s\nSTDERR: %s",
                result.returncode, result.stdout[-2000:], result.stderr[-2000:],
            )
        return result

    def get_project_info(
        self, context: Optional[AssetExecutionContext] = None
    ) -> dict:
        """
        Model and macro counts, parsed from `sqlmesh info` text output.

        --skip-connection because we want the counts, not a warehouse probe:
        the probe is a second open of the catalog mid-pipeline, and on the
        DuckDB file backend that is one more chance to collide with a writer.

        Returns {} with a logged reason rather than silently -- the previous
        version swallowed every failure, which is how models_count could read 0
        forever without anyone learning the flag had been rejected.
        """
        result = subprocess.run(
            ["sqlmesh", "info", "--skip-connection"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self._cwd(),
            env=self._env(),
            check=False,
        )

        if result.returncode != 0:
            message = (result.stderr or result.stdout or "").strip().splitlines()
            if context:
                context.log.warning(
                    "`sqlmesh info` failed (exit %s): %s",
                    result.returncode, message[0] if message else "no output",
                )
            return {}

        text = result.stdout or ""
        models = _MODEL_COUNT.search(text)
        macros = _MACRO_COUNT.search(text)

        if models is None and context:
            context.log.debug(
                "Could not parse a model count from `sqlmesh info`. The output "
                "format changed; the raw text is in the asset metadata."
            )

        return {
            "models": int(models.group(1)) if models else None,
            "macros": int(macros.group(1)) if macros else None,
            "raw": text.strip(),
        }