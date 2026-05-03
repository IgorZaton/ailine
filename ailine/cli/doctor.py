"""``ailine doctor``: single user-facing validation/health-check surface.

`doctor` is the only command users should reach for to verify their AIline
setup. It reuses :func:`ailine.config.validate.validate_config`, so any
schema rule used by ``ailine track`` is also checked here.

Checks are small callables returning :class:`CheckResult` so adding a new one
(OCP) is just a function + an entry in ``DEFAULT_CHECKS``.
"""

from __future__ import annotations

import json as _json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Callable, List, Optional

import click

from ailine.config import constants
from ailine.config.validate import (
    ConfigValidationError,
    ValidatedConfig,
    validate_config,
)
from ailine.fingerprint.env import collect_environment_fingerprint
from ailine.integrations.git_root import resolve_git_root


PASS = "pass"
WARN = "warn"
FAIL = "fail"


@dataclass
class CheckResult:
    name: str
    status: str
    detail: str

    def to_dict(self) -> dict:
        return {"name": self.name, "status": self.status, "detail": self.detail}


@dataclass
class DoctorContext:
    cwd: str
    config_path: Optional[str]
    config: Optional[ValidatedConfig]


def _check_config(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult(
            "config",
            FAIL,
            f"Config validation failed (see error above). Path: {ctx.config_path or constants.POLICY_PATH}",
        )
    if not ctx.config.config_exists:
        return CheckResult(
            "config",
            FAIL,
            f"No .ailine.yml at {ctx.config.config_path}. Create one (see docs/track-contract.md).",
        )
    if ctx.config.warnings:
        return CheckResult(
            "config",
            WARN,
            "; ".join(ctx.config.warnings),
        )
    return CheckResult("config", PASS, f"OK ({ctx.config.config_path})")


def _check_git(ctx: DoctorContext) -> CheckResult:
    repo_root_cfg = (ctx.config.track["repo_root"] if ctx.config else "auto") or "auto"
    try:
        root = resolve_git_root(ctx.cwd, repo_root_cfg)
    except FileNotFoundError as exc:
        return CheckResult("git", FAIL, str(exc))

    head_file = os.path.join(root, ".git", "HEAD")
    if not os.path.exists(head_file):
        return CheckResult("git", WARN, f"Repo at {root} but HEAD missing (worktree?).")
    return CheckResult("git", PASS, f"work-tree at {root}")


def _check_mlflow(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("mlflow", FAIL, "config invalid")
    mode = ctx.config.track["mlflow"]["mode"]
    set_env = ctx.config.track["mlflow"]["set_env"]

    if mode == "none" and not set_env:
        return CheckResult("mlflow", PASS, "mode=none, no MLflow side effects")

    tracking_uri = os.environ.get("AILINE_MLFLOW_URI") or constants.MLFLOW_TRACKING_URI
    detail = f"mode={mode} set_env={set_env} tracking_uri={tracking_uri}"
    if not tracking_uri:
        return CheckResult("mlflow", FAIL, "no tracking URI resolvable (AILINE_MLFLOW_URI / default)")
    return CheckResult("mlflow", PASS, detail)


def _check_dvc(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("dvc", FAIL, "config invalid")
    verify_cmds = ctx.config.track["dvc"]["verify_commands"]
    if not verify_cmds:
        return CheckResult("dvc", PASS, "no verify_commands configured")

    if shutil.which("dvc") is None:
        return CheckResult(
            "dvc",
            FAIL,
            "track.dvc.verify_commands set but `dvc` CLI is not on PATH",
        )
    proc = subprocess.run(
        ["dvc", "--version"], check=False, capture_output=True, text=True
    )
    if proc.returncode != 0:
        return CheckResult("dvc", FAIL, "dvc --version failed")
    return CheckResult("dvc", PASS, f"dvc {proc.stdout.strip()} present")


def _writable(path: str) -> bool:
    parent = os.path.dirname(os.path.abspath(path)) or "."
    return os.access(parent, os.W_OK)


def _check_storage(ctx: DoctorContext) -> CheckResult:
    state_dir = constants.STATE_DIR
    snapshot_dir = constants.DEFAULT_STORAGE_DIR
    db_path = constants.DB_PATH

    issues: List[str] = []
    for name, target in (("STATE_DIR", state_dir), ("SNAPSHOT_DIR", snapshot_dir)):
        os.makedirs(target, exist_ok=True)
        if not os.access(target, os.W_OK):
            issues.append(f"{name}={target} not writable")
    if not _writable(db_path):
        issues.append(f"DB_PATH={db_path} not writable")

    if issues:
        return CheckResult("storage", FAIL, "; ".join(issues))
    return CheckResult(
        "storage",
        PASS,
        f"state={state_dir} snapshots={snapshot_dir} db={db_path}",
    )


def _check_environment(ctx: DoctorContext) -> CheckResult:
    if ctx.config is None:
        return CheckResult("environment", FAIL, "config invalid")
    fingerprint, status = collect_environment_fingerprint(ctx.cwd, ctx.config.environment)
    if status == "complete":
        pkgs = ", ".join(f"{k}={v}" for k, v in fingerprint["packages"].items())
        return CheckResult(
            "environment",
            PASS,
            f"python={fingerprint['python_version']} platform={fingerprint['platform']} {pkgs}",
        )
    if status == "missing":
        return CheckResult("environment", WARN, "fingerprint disabled in config")
    missing_pkgs = [k for k, v in fingerprint.get("packages", {}).items() if v is None]
    detail = (
        "partial fingerprint: "
        f"poetry.lock={'present' if fingerprint.get('poetry_lock_sha256') else 'missing'} "
        f"missing_packages={missing_pkgs or 'none'}"
    )
    return CheckResult("environment", WARN, detail)


DEFAULT_CHECKS: List[Callable[[DoctorContext], CheckResult]] = [
    _check_config,
    _check_git,
    _check_mlflow,
    _check_dvc,
    _check_storage,
    _check_environment,
]


def run_checks(ctx: DoctorContext) -> List[CheckResult]:
    return [check(ctx) for check in DEFAULT_CHECKS]


def _build_context(config_path: Optional[str]) -> DoctorContext:
    cwd = os.getcwd()
    try:
        cfg = validate_config(config_path)
    except ConfigValidationError as exc:
        click.echo(f"config error: {exc.message}", err=True)
        cfg = None
    return DoctorContext(cwd=cwd, config_path=config_path, config=cfg)


@click.command("doctor", help="Validate .ailine.yml and check the local environment.")
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to .ailine.yml (defaults to AILINE_POLICY_PATH or ./.ailine.yml).",
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON results.")
@click.option(
    "--strict",
    is_flag=True,
    help="Treat warnings as failures (exit 1).",
)
def doctor_command(config_path: Optional[str], as_json: bool, strict: bool):
    ctx = _build_context(config_path)
    results = run_checks(ctx)

    if as_json:
        click.echo(_json.dumps([r.to_dict() for r in results], indent=2))
    else:
        for r in results:
            click.echo(f"[{r.status.upper():4}] {r.name:12} {r.detail}")

    has_fail = any(r.status == FAIL for r in results)
    has_warn = any(r.status == WARN for r in results)
    if has_fail or (strict and has_warn):
        sys.exit(1)
    sys.exit(0)
