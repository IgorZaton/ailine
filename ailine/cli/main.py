"""``ailine`` Click entrypoint and command orchestration."""

import logging
import os
import subprocess
import sys

import click
import mlflow

from ailine.cli.doctor import doctor_command
from ailine.cli.formatting import print_formatted_data, print_table
from ailine.cli.init import (
    init_demo_command,
    init_workspace_command,
    reset_demo_command,
)
from ailine.cli.manage import purge_command, remove_command
from ailine.cli.prune import prune_legacy_snapshots_command
from ailine.cli.restore import restore_command
from ailine.cli.track import track_command
from ailine.config import constants
from ailine.config.validate import ConfigValidationError, validate_config
from ailine.integrations.mlflow_links import resolve_mlflow_ui_url
from ailine.persistence import repository
from ailine.persistence.db import init_db
from ailine.run.migration import migrate_legacy_state_artifacts
from ailine.run.session import SessionError, run_tracked_command
from ailine.snapshot.storage import resolve_storage_dir
from ailine.web.state import get_repo_url, load_repo_url


@click.group()
def cli():
    # Relocate any pre-``.ailine/`` artifacts BEFORE opening DB/log so the new
    # paths are used from this invocation onwards. Best-effort: failures fall
    # back to legacy paths instead of aborting the user's command.
    migrate_legacy_state_artifacts()
    init_db()
    load_repo_url()
    mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
    logging.info(f"MLflow tracking URI set to {constants.MLFLOW_TRACKING_URI}")


@cli.command()
@click.option("--verbose", is_flag=True, help="Show all data about each experiment in a terminal")
def status(verbose):
    if not os.path.exists(constants.DB_PATH):
        raise click.ClickException(
            f"AIline database not found at {constants.DB_PATH}. "
            "Run 'ailine init-workspace' (or 'ailine init-demo') and 'ailine track --' "
            "(or 'ailine run') first."
        )
    tree = repository.fetch_status_rows()
    if not tree:
        click.echo("No experiments recorded yet. Use 'ailine run' to log one.")
        return
    if verbose:
        print_formatted_data(tree)
    else:
        print_table(tree)


@cli.command(
    "run",
    help=(
        "Demo flow: snapshot ./repo, optionally `dvc add` a dataset, then run "
        "`python <script>` under MLflow. For your own projects use 'ailine track --'."
    ),
)
@click.option("--script", default="train.py", help="Script (relative to ./repo) to execute.")
@click.option(
    "--dataset",
    default="data.csv",
    help="Dataset path inside ./repo. Only used when --dvc-add is set.",
)
@click.option(
    "--dvc-add/--no-dvc-add",
    "dvc_add",
    default=False,
    show_default=True,
    help=(
        "Run 'dvc add <dataset>' before training. Off by default; the demo uses "
        "this to version a synthetic dataset. Real workflows should manage DVC "
        "themselves and rely on 'ailine track --'."
    ),
)
@click.option(
    "--name",
    "record_label",
    default=None,
    help="Human-readable name for this run (default: random adjective-animal).",
)
def run(script, dataset, dvc_add, record_label):
    repo_url = get_repo_url()
    if not repo_url:
        raise click.UsageError(
            "Demo not initialized. Run 'ailine init-demo <repo_url>' first."
        )
    if not os.path.exists(constants.REPO_DIR):
        raise click.UsageError(
            f"Repo directory {constants.REPO_DIR} not found. Re-run 'ailine init-demo'."
        )
    if not os.path.exists(os.path.join(constants.REPO_DIR, script)):
        raise click.UsageError(f"Script {script} not found in {constants.REPO_DIR}")

    try:
        config = validate_config()
    except ConfigValidationError as exc:
        raise click.ClickException(str(exc)) from exc

    if dvc_add:
        if not os.path.exists(os.path.join(constants.REPO_DIR, dataset)):
            raise click.UsageError(f"Dataset {dataset} not found in {constants.REPO_DIR}")
        subprocess.run(["dvc", "add", dataset], check=True, cwd=constants.REPO_DIR)

    # Demo training scripts do not start their own MLflow run, so wrap them.
    config.track["mlflow"]["mode"] = "wrap"

    git_root = os.path.abspath(constants.REPO_DIR)
    storage = resolve_storage_dir(config.snapshot, git_root)

    def _preview_demo(rec: str, mlf: str) -> None:
        click.echo(
            f"ailine run: repo={git_root} name={rec!r} mlflow_run_name={mlf!r} script={script}",
            err=True,
        )

    def _announce_demo_started(record_id: str, mlflow_run_id) -> None:
        click.echo(
            f"ailine run: tracking record={record_id} status=in_progress",
            err=True,
        )
        if mlflow_run_id:
            url = resolve_mlflow_ui_url(mlflow_run_id)
            if url:
                click.echo(
                    f"ailine run: MLflow run={mlflow_run_id} url={url}",
                    err=True,
                )

    try:
        result = run_tracked_command(
            git_root=git_root,
            argv=["python", script],
            storage=storage,
            config=config,
            record_name=record_label,
            on_resolved_labels=_preview_demo,
            on_run_started=_announce_demo_started,
        )
    except SessionError as exc:
        raise click.ClickException(str(exc)) from exc

    logging.info(
        f"Demo run logged: name={result.record_name!r} mlflow={result.mlflow_run_id} "
        f"commit={result.commit_id}"
    )
    click.echo(
        f"Demo run logged: name={result.record_name!r} mlflow={result.mlflow_run_id} "
        f"commit={result.commit_id}"
    )
    sys.exit(result.exit_code)


@cli.command()
def serve():
    """Launch the local MLflow UI and the ailine Flask web app."""
    from ailine.integrations.mlflow_ui import start_mlflow_ui
    from ailine.web.app import app

    start_mlflow_ui()
    app.run(host="0.0.0.0", port=5000, debug=True)


cli.add_command(init_workspace_command)
cli.add_command(init_demo_command)
cli.add_command(reset_demo_command)
cli.add_command(track_command)
cli.add_command(doctor_command)
cli.add_command(prune_legacy_snapshots_command)
cli.add_command(restore_command)
cli.add_command(remove_command)
cli.add_command(purge_command)


def main():
    """Console-script entry point.

    Click handles parsing and command dispatch. Use ``ailine serve`` to start
    the MLflow UI and Flask web app together (replaces the legacy
    auto-launch from ``python ailine.py``).
    """
    cli()


if __name__ == "__main__":
    main()
