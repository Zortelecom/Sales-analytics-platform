"""SQLMesh integration resource for Dagster"""
import subprocess
import os
from typing import List, Optional
import json
from dagster import ConfigurableResource, AssetExecutionContext, Failure, MetadataValue
from pydantic import Field


class SQLMeshResource(ConfigurableResource):
    """Resource for running SQLMesh commands"""
    project_path: str = Field(
        default="sqlmesh",
        description="Path to SQLMesh project directory"
    )
    environment: str = Field(
        default="dev",
        description="SQLMesh environment to target"
    )
    start_date: Optional[str] = Field(
        default="2025-01-01",
        description="Start date for incremental models (format: YYYY-MM-DD). "
                    "Use this to force processing from a specific date."
    )

    def _run_command(self, cmd: List[str], context: Optional[AssetExecutionContext] = None) -> subprocess.CompletedProcess:
        """Execute SQLMesh CLI command"""
        # SQLMesh commands often take env as a positional arg, not a flag.
        full_cmd = ["sqlmesh"] + cmd

        if context:
            context.log.info(f"Running: {' '.join(full_cmd)}")

        # encoding + PYTHONIOENCODING are load-bearing on Windows.
        # SQLMesh prints a ✅ in its audit summary; the console default is
        # cp1252, and capturing it raised
        #   'charmap' codec can't encode character '\u2705'
        # AFTER the audits had already run -- so a perfectly good audit pass
        # was reported as an error because of one character in the output.
        result = subprocess.run(
            full_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.project_path,
            env={
                **os.environ,
                "PYTHONPATH": os.getcwd(),
                "PYTHONIOENCODING": "utf-8",
            },
            check=False,
        )

        if result.returncode != 0:
            error_msg = f"""SQLMesh command failed with exit code
                {result.returncode}\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"""
            if context:
                context.log.error(error_msg)
            raise Failure(f"SQLMesh error: {result.stderr or result.stdout}")

        if context and result.stdout:
            warning_lines = [
                line.strip()
                for line in result.stdout.splitlines()
                if "WARNING" in line.upper() or "WARN" in line.upper()
            ]
            if warning_lines:
                warnings_text = "\n".join(warning_lines)
                context.log.warning(
                    "SQLMesh emitted %d warning(s); attaching to asset metadata",
                    len(warning_lines),
                )
                context.add_output_metadata({
                    "sqlmesh_warnings": MetadataValue.text(warnings_text)
                })

        return result

    def plan(self, context: AssetExecutionContext, 
            auto_apply: bool = True, start_date: Optional[str] = None):
        """Run SQLMesh plan
        
        Args:
            context: Dagster execution context
            auto_apply: Whether to auto-apply the plan
            start_date: Override the default start_date for this run (format: YYYY-MM-DD)
        """
        # Pass environment as a positional argument
        cmd = ["plan", self.environment]

        # ✅ ADDED: Include start date to ensure full data processing from 2025-01-01
        # This forces SQLMesh to process all intervals from the start date,
        # mimicking "first run" behavior for incremental models
        effective_start = start_date or self.start_date
        if effective_start:
            cmd.extend(["--start", effective_start])
            if context:
                context.log.info(
                    f"SQLMesh plan will start from: {effective_start}")

        if auto_apply:
            cmd.append("--auto-apply")

        result = self._run_command(cmd, context)
        context.log.info(f"SQLMesh plan output:\n{result.stdout}")
        return result

    def run_transformations(self, context: AssetExecutionContext, select: Optional[str] = None):
        """Run SQLMesh run (execute models)"""
        # ✅ FIX: Pass environment as a positional argument
        cmd = ["run", self.environment]

        if select:
            cmd.extend(["--select", select])

        result = self._run_command(cmd, context)
        context.log.info("SQLMesh run completed")
        return result

    def audit(self, context: AssetExecutionContext, start_date: Optional[str] = None):
        """Run SQLMesh audits for the target environment.

        (2026-08) Passes the environment and start date. `sqlmesh audit` with
        no arguments audits the DEFAULT environment over the default window --
        not necessarily the one just planned, which is how a dev plan could be
        followed by a prod audit without anything looking wrong.
        """
        cmd = ["audit", "--environment", self.environment]
        effective_start = start_date or self.start_date
        if effective_start:
            cmd.extend(["--start", effective_start])
        result = self._run_command(cmd, context)
        context.log.info("SQLMesh audit completed")
        return result

    def get_model_info(self) -> dict:
        """Get information about SQLMesh models"""
        result = subprocess.run(
            ["sqlmesh", "info", "--format", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=self.project_path,
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            check=False,
        )
        return json.loads(result.stdout) if result.returncode == 0 else {}