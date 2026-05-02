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

from ailine.config import constants
from ailine.config.validate import (
    ConfigValidationError,
    validate_config,
)
from ailine.integrations.git_root import origin_url, resolve_git_root
from ailine.run.session import SessionError, run_tracked_command


@click.command(
    "track",
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
    help=(
        "Run a command under AIline tracking. Use '--' to separate ailine flags from "
        "the command, e.g.  ailine track -- python train.py --epochs 5"
    ),
)
@click.option(
    "--storage",
    default=constants.DEFAULT_STORAGE_DIR,
    show_default=True,
    help="Directory where snapshot bundles are written.",
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
    help="MLflow run name when track.mlflow.mode == 'wrap'.",
)
@click.argument("argv", nargs=-1, type=click.UNPROCESSED, required=True)
def track_command(storage: str, config_path: str, run_name: str, argv: tuple):
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

    click.echo(f"ailine track: repo={git_root} cmd={' '.join(argv)}", err=True)

    try:
        result = run_tracked_command(
            git_root=git_root,
            argv=list(argv),
            storage=storage,
            config=config,
            git_url_hint=origin_url(git_root),
            run_name=run_name,
        )
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(
        f"ailine track: recorded {result.commit_type}={result.commit_id} "
        f"exit={result.exit_code}",
        err=True,
    )
    sys.exit(result.exit_code)
