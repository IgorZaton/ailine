"""``ailine`` Click entrypoint and command orchestration."""

import json
import logging
import os
import shutil
import subprocess
from datetime import datetime

import click
import git
import mlflow

from ailine.cli.formatting import print_formatted_data, print_table
from ailine.config import constants
from ailine.config.defaults import CommitType
from ailine.config.loader import (
    load_dvc_config,
    load_environment_config,
    load_run_capture_config,
    load_snapshot_policy,
)
from ailine.fingerprint.env import collect_environment_fingerprint
from ailine.integrations.git_url import normalize_git_url
from ailine.linkage.dvc import build_dvc_linkage
from ailine.persistence import repository
from ailine.persistence.db import init_db
from ailine.persistence.repository import RunRecord
from ailine.run.capture import build_run_command_payload
from ailine.snapshot.archive import create_snapshot
from ailine.snapshot.manifest import build_manifest
from ailine.snapshot.paths import normalize_rel_path
from ailine.snapshot.scan import resolve_large_file_decisions, scan_repo_files
from ailine.web.state import get_repo_url, load_repo_url, set_repo_url


@click.group()
def cli():
    init_db()
    load_repo_url()
    mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
    logging.info(f"MLflow tracking URI set to {constants.MLFLOW_TRACKING_URI}")


@cli.command()
@click.argument("repo_url")
def init(repo_url):
    if os.path.exists(constants.REPO_DIR):
        raise click.UsageError(
            f"Directory {constants.REPO_DIR} already exists. Run 'cleanup' first."
        )
    subprocess.run(["git", "clone", repo_url, constants.REPO_DIR], check=True)
    with open(constants.CONFIG_PATH, "w") as f:
        f.write(repo_url)
    subprocess.run(["git", "fetch"], check=True, cwd=constants.REPO_DIR)
    set_repo_url(repo_url)
    logging.info(f"Initialized AIline with {repo_url} in {constants.REPO_DIR}")
    print(f"Initialized AIline with {repo_url} in {constants.REPO_DIR}")


@cli.command()
@click.option("--verbose", is_flag=True, help="Show all data about each experiment in a terminal")
def status(verbose):
    if not os.path.exists(constants.DB_PATH):
        return "Database not found. Run 'ailine init' and 'ailine run' first.", 500
    tree = repository.fetch_status_rows()
    if verbose:
        print_formatted_data(tree)
    else:
        print_table(tree)


@cli.command()
@click.option("--script", default="train.py", help="Script to run")
@click.option("--dataset", default="data.csv", help="Dataset file")
@click.option(
    "--storage", default=constants.DEFAULT_STORAGE_DIR, help="Directory to store snapshots"
)
def run(script, dataset, storage):
    repo_url = get_repo_url()
    if not repo_url:
        raise click.UsageError("AIline not initialized. Run 'ailine init <repo_url>' first.")
    if not os.path.exists(constants.REPO_DIR):
        raise click.UsageError(f"Repo directory {constants.REPO_DIR} not found. Re-run 'init'.")
    if not os.path.exists(os.path.join(constants.REPO_DIR, script)):
        raise click.UsageError(f"Script {script} not found in {constants.REPO_DIR}")
    if not os.path.exists(os.path.join(constants.REPO_DIR, dataset)):
        raise click.UsageError(f"Dataset {dataset} not found in {constants.REPO_DIR}")

    repo = git.Repo(constants.REPO_DIR)
    latest_commit = repo.head.commit.hexsha
    git_url = normalize_git_url(repo_url, latest_commit)

    manifest_path = None
    metadata_path = None
    archive_bytes = None
    included_file_count = None
    excluded_file_count = None
    large_file_pointer_count = None
    diff_path = None

    if repo.is_dirty(untracked_files=True):
        policy = load_snapshot_policy()
        entries = scan_repo_files(constants.REPO_DIR, policy)
        entries, _store = resolve_large_file_decisions(entries, policy)
        manifest_entries, archive_entries, manifest_extra = build_manifest(entries, storage)
        diff_text = repo.git.diff("HEAD")
        untracked_files = [normalize_rel_path(p) for p in repo.untracked_files]
        snapshot_result = create_snapshot(
            manifest_entries=manifest_entries,
            archive_entries=archive_entries,
            parent_commit_hash=latest_commit,
            storage_dir=storage,
            diff_text=diff_text,
            untracked_files=untracked_files,
        )
        commit_id = snapshot_result["snapshot_hash"]
        snapshot_path = snapshot_result["snapshot_path"]
        manifest_path = snapshot_result["manifest_path"]
        metadata_path = snapshot_result["metadata_path"]
        archive_bytes = snapshot_result["archive_bytes"]
        diff_path = snapshot_result["diff_path"]
        commit_type = CommitType.SNAPSHOT
        parent = latest_commit[:7]
        included_file_count = manifest_extra["summary"]["included_file_count"]
        excluded_file_count = manifest_extra["summary"]["excluded_file_count"]
        large_file_pointer_count = manifest_extra["summary"]["large_file_pointer_count"]
        click.echo(
            "Snapshot preflight: "
            f"files={len(entries)} included={included_file_count} "
            f"excluded={excluded_file_count} pointers={large_file_pointer_count} "
            f"included_bytes={manifest_extra['summary']['included_bytes']}"
        )
    else:
        commit_id = latest_commit
        commit_type = CommitType.GIT
        snapshot_path = None
        parent = None

    dvc_cfg = load_dvc_config()
    env_cfg = load_environment_config()
    run_capture_cfg = load_run_capture_config()
    original_dir = os.getcwd()
    os.chdir(constants.REPO_DIR)
    try:
        subprocess.run(["dvc", "add", dataset], check=True)
        dvc_linkage = build_dvc_linkage(os.getcwd(), dvc_cfg)
        env_fingerprint, env_fingerprint_status = collect_environment_fingerprint(
            original_dir, env_cfg
        )
        run_command_payload, run_command_summary = build_run_command_payload(
            script, dataset, storage, os.getcwd()
        )
        if not run_capture_cfg.get("enabled", True):
            run_command_payload = {}
            run_command_summary = None
        dvc_version = f"dataset_001_v{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        with mlflow.start_run(run_name=f"exp_{commit_id[:8]}"):
            subprocess.run(["python", script], check=True)
            mlflow.set_tag(
                "commit" if commit_type == CommitType.GIT else CommitType.SNAPSHOT.value,
                commit_id,
            )
            mlflow.set_tag("dataset", dvc_version)
            mlflow.set_tag("dvc_linkage_status", dvc_linkage["status"])
            mlflow.set_tag("env_fingerprint_status", env_fingerprint_status)
            mlflow.set_tag("run.script", script)
            mlflow.set_tag("run.dataset", dataset)
            run_id = mlflow.active_run().info.run_id
    finally:
        os.chdir(original_dir)

    record = RunRecord(
        id=commit_id,
        type=commit_type.value,
        parent=parent,
        mlflow_run=run_id,
        dvc_version=dvc_version,
        snapshot_path=snapshot_path,
        timestamp=datetime.now().isoformat(),
        git_url=git_url if commit_type == CommitType.GIT else None,
        manifest_path=manifest_path,
        metadata_path=metadata_path,
        archive_bytes=archive_bytes,
        included_file_count=included_file_count,
        excluded_file_count=excluded_file_count,
        large_file_pointer_count=large_file_pointer_count,
        diff_path=diff_path,
        dvc_linkage_json=json.dumps(dvc_linkage, sort_keys=True),
        dvc_linkage_status=dvc_linkage["status"],
        env_fingerprint_json=json.dumps(env_fingerprint, sort_keys=True),
        env_fingerprint_status=env_fingerprint_status,
        run_command_json=(
            json.dumps(run_command_payload, sort_keys=True) if run_command_payload else None
        ),
        run_command_summary=run_command_summary,
    )
    repository.insert_run(record)
    logging.info(f"Experiment logged: {run_id} tied to {commit_id}")
    print(f"Experiment logged: {run_id} tied to {commit_id}")


@cli.command()
def serve():
    """Launch the local MLflow UI and the ailine Flask web app."""
    from ailine.integrations.mlflow_ui import start_mlflow_ui
    from ailine.web.app import app

    start_mlflow_ui()
    app.run(host="0.0.0.0", port=5000, debug=True)


@cli.command()
def cleanup():
    items_to_remove = [
        constants.MLFLOW_STORAGE_DIR,
        constants.REPO_DIR,
        constants.DB_PATH,
        constants.CONFIG_PATH,
        constants.DEFAULT_STORAGE_DIR,
    ]
    for item in os.listdir("."):
        if item.startswith("temp_") and os.path.isdir(item):
            items_to_remove.append(item)

    for item in items_to_remove:
        if os.path.isdir(item):
            shutil.rmtree(item, ignore_errors=True)
            logging.info(f"Removed directory: {item}")
            print(f"Removed directory: {item}")
        elif os.path.isfile(item):
            os.remove(item)
            logging.info(f"Removed file: {item}")
            print(f"Removed file: {item}")

    set_repo_url(None)
    logging.info("Cleanup complete")
    print("Cleanup complete. Run 'ailine init <repo_url>' to start fresh.")


def main():
    """Console-script entry point.

    Click handles parsing and command dispatch. Use ``ailine serve`` to start
    the MLflow UI and Flask web app together (replaces the legacy
    auto-launch from ``python ailine.py``).
    """
    cli()


if __name__ == "__main__":
    main()
