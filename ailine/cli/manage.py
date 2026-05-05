"""``ailine remove <id>`` and ``ailine purge``: project-state cleanup commands.

Both commands wrap planning helpers in :mod:`ailine.run.cleanup`. The CLI
layer's responsibility is narrow: resolve config, render a human-readable
summary of what will change, prompt for confirmation only where the
destructive blast radius warrants it (purge), and call the apply helpers.
"""

from __future__ import annotations

import os
from typing import Optional

import click

from ailine.config.validate import ConfigValidationError, validate_config
from ailine.integrations.git_root import resolve_git_root
from ailine.run.cleanup import (
    apply_purge_plan,
    apply_remove_plan,
    plan_purge_workspace,
    plan_remove_record,
)
from ailine.snapshot.storage import resolve_storage_dir


def _resolve_with_mlflow(cli_value: Optional[bool], cleanup_cfg: dict) -> bool:
    """CLI flag wins over YAML, which wins over the built-in default ``False``."""
    if cli_value is not None:
        return bool(cli_value)
    remove_cfg = cleanup_cfg.get("remove") if isinstance(cleanup_cfg, dict) else None
    if isinstance(remove_cfg, dict) and isinstance(remove_cfg.get("with_mlflow"), bool):
        return remove_cfg["with_mlflow"]
    return False


def _resolve_repo_root(config) -> str:
    try:
        return resolve_git_root(os.getcwd(), config.track["repo_root"])
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc


@click.command(
    "remove",
    help=(
        "Delete the lineage record <id> and its on-disk artifacts (manifest, "
        "metadata, diff, plus content-addressed objects only this row owned). "
        "Use --with-mlflow true|false to override cleanup.remove.with_mlflow "
        "from .ailine.yml. --dry-run prints the plan without changes."
    ),
)
@click.argument("record_id", required=True)
@click.option(
    "--with-mlflow",
    "with_mlflow",
    type=click.BOOL,
    default=None,
    help=(
        "Also delete the linked MLflow run (when this row carries one). "
        "Defaults to cleanup.remove.with_mlflow in .ailine.yml, otherwise false."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be removed without touching the DB or filesystem.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to .ailine.yml (defaults to AILINE_POLICY_PATH or ./.ailine.yml).",
)
def remove_command(
    record_id: str,
    with_mlflow: Optional[bool],
    dry_run: bool,
    config_path: Optional[str],
) -> None:
    try:
        config = validate_config(config_path)
    except ConfigValidationError as exc:
        raise click.ClickException(str(exc)) from exc

    for warning in config.warnings:
        click.echo(f"warning: {warning}", err=True)

    git_root = _resolve_repo_root(config)
    storage_dir = resolve_storage_dir(config.snapshot, git_root)
    effective_with_mlflow = _resolve_with_mlflow(with_mlflow, config.cleanup)

    try:
        plan = plan_remove_record(record_id, storage_dir=storage_dir)
    except LookupError:
        raise click.ClickException(
            f"Record not found: {record_id}. "
            "Use `ailine status` to list available record IDs."
        )

    verb = "would remove" if dry_run else "removing"
    name = plan.record_name or "-"
    click.echo(
        f"ailine remove: {verb} record={plan.record_id} name={name!r} "
        f"type={plan.record_type or '-'} mlflow_run={plan.mlflow_run_id or '-'} "
        f"artifact_files={len(plan.artifact_files)} "
        f"orphan_objects={len(plan.orphan_object_paths)} "
        f"with_mlflow={effective_with_mlflow}"
    )
    for path in plan.artifact_files:
        sub = "would remove" if dry_run else "remove"
        click.echo(f"  {sub}: {path}")
    for path in plan.orphan_object_paths:
        sub = "would remove" if dry_run else "remove"
        click.echo(f"  {sub} (object): {path}")

    if dry_run:
        return

    apply_remove_plan(
        plan,
        with_mlflow=effective_with_mlflow,
    )
    click.echo(f"ailine remove: done id={plan.record_id}")


@click.command(
    "purge",
    help=(
        "Remove all AIline state and workspace config from this project: "
        ".ailine/, .ailine.yml, .ailineignore, plus any non-default snapshot "
        "storage_dir configured outside .ailine/. Leaves mlruns/ and repo/ "
        "untouched. Asks for confirmation before deleting; --dry-run skips "
        "the prompt and prints the plan only."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be removed without touching the filesystem.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to .ailine.yml (defaults to AILINE_POLICY_PATH or ./.ailine.yml).",
)
def purge_command(dry_run: bool, config_path: Optional[str]) -> None:
    try:
        config = validate_config(config_path)
    except ConfigValidationError as exc:
        raise click.ClickException(str(exc)) from exc

    for warning in config.warnings:
        click.echo(f"warning: {warning}", err=True)

    try:
        git_root = resolve_git_root(os.getcwd(), config.track["repo_root"])
    except FileNotFoundError:
        # Purge must work even when the repo is not a git work-tree (e.g. an
        # `init-workspace` run that never tracked anything). Fall back to the
        # current working directory in that case.
        git_root = os.getcwd()

    plan = plan_purge_workspace(git_root, snapshot_cfg=config.snapshot)

    targets = plan.all_paths()
    if not targets and plan.db_row_count == 0:
        click.echo("ailine purge: nothing to remove (no AIline state in this project).")
        return

    verb = "would remove" if dry_run else "will remove"
    click.echo(
        f"ailine purge: {verb} {len(targets)} path(s); db_rows={plan.db_row_count}"
    )
    for path in targets:
        kind = "dir" if os.path.isdir(path) else "file"
        click.echo(f"  {kind}: {path}")

    if dry_run:
        return

    click.confirm(
        "All AIline files listed above will be removed. Confirm?",
        default=False,
        abort=True,
    )

    apply_purge_plan(plan)
    click.echo("ailine purge: done.")
