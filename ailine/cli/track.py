"""``ailine track -- <argv...>`` command.

Thin Click wrapper around :func:`ailine.run.session.run_tracked_command`.
Responsibilities limited to:

* parse argv after ``--``
* discover the Git work-tree from cwd (or honour ``track.repo_root``)
* validate ``.ailine.yml`` once via :func:`ailine.config.validate.validate_config`
* propagate child exit code back to the shell
"""

from __future__ import annotations

import os
import sys

import click

from ailine.config.validate import (
    ConfigValidationError,
    validate_config,
)
from ailine.integrations.git_root import origin_url, resolve_git_root
from ailine.run.session import SessionError, run_tracked_command
from ailine.snapshot.storage import resolve_storage_dir


@click.command(
    "track",
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
    help=(
        "Run a command under AIline tracking. Use '--' to separate ailine flags from "
        "the command, e.g.  ailine track -- python train.py --epochs 5"
    ),
)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to .ailine.yml (defaults to AILINE_POLICY_PATH or ./.ailine.yml).",
)
@click.option(
    "--run-name",
    default=None,
    help=(
        "MLflow run name when track.mlflow.mode == 'wrap'. If set without --name, "
        "the same value is stored as the lineage record name (aligned traceability). "
        "If both --name and --run-name are set, --name is the DB label and --run-name "
        "is used only for MLflow."
    ),
)
@click.option(
    "--name",
    "record_label",
    default=None,
    help=(
        "Human-readable name for this run (status table, web UI, and MLflow run name "
        "in wrap mode unless --run-name overrides MLflow). "
        "Default: random adjective-animal from fixed word lists."
    ),
)
@click.argument("argv", nargs=-1, type=click.UNPROCESSED, required=True)
def track_command(config_path: str, run_name: str, record_label, argv: tuple):
    if not argv:
        raise click.UsageError("Provide a command to run after '--'.")

    try:
        config = validate_config(config_path)
    except ConfigValidationError as exc:
        raise click.ClickException(str(exc)) from exc

    if not config.config_exists:
        raise click.ClickException(
            f"No .ailine.yml found at {config.config_path}. "
            "Create one (see docs/track-contract.md) or pass --config."
        )

    for warning in config.warnings:
        click.echo(f"warning: {warning}", err=True)

    try:
        git_root = resolve_git_root(os.getcwd(), config.track["repo_root"])
    except FileNotFoundError as exc:
        raise click.ClickException(str(exc)) from exc

    storage = resolve_storage_dir(config.snapshot, git_root)

    def _preview_labels(rec: str, mlf: str) -> None:
        mode = config.track["mlflow"]["mode"]
        cmd = " ".join(argv)
        if mode == "wrap":
            click.echo(
                f"ailine track: repo={git_root} name={rec!r} mlflow_run_name={mlf!r} cmd={cmd}",
                err=True,
            )
        else:
            click.echo(f"ailine track: repo={git_root} name={rec!r} cmd={cmd}", err=True)

    try:
        result = run_tracked_command(
            git_root=git_root,
            argv=list(argv),
            storage=storage,
            config=config,
            git_url_hint=origin_url(git_root),
            run_name=run_name,
            record_name=record_label,
            on_resolved_labels=_preview_labels,
        )
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"ailine track: recorded name={result.record_name!r} "
        f"{result.commit_type}={result.commit_id} exit={result.exit_code}",
        err=True,
    )
    sys.exit(result.exit_code)
