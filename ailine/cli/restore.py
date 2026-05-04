"""``ailine restore <snapshot_id>``: strict-sync materialization of a snapshot.

Restores the worktree to the exact state captured by an ``objects-v1``
snapshot. Any file currently present that is not part of the snapshot's
include set is removed (within repo scope; ``.git`` and ``.ailine`` are
always preserved).

Default behavior is fail-fast: aborts on a dirty working tree unless
``--force`` is given. Use ``--dry-run`` to preview the write/delete plan
without touching the filesystem.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import click
import git

from ailine.config.validate import ConfigValidationError, validate_config
from ailine.integrations.git_root import resolve_git_root
from ailine.persistence import repository
from ailine.snapshot.archive import SNAPSHOT_FORMAT_OBJECTS_V1
from ailine.snapshot.ignore import load_ignore_spec
from ailine.snapshot.restore import (
    PROTECTED_DIR_NAMES,
    apply_restore,
    plan_restore,
)
from ailine.snapshot.storage import resolve_storage_dir


def _all_dirty_paths(repo: git.Repo) -> list[str]:
    """Return all dirty paths (tracked + untracked), stable-sorted."""
    paths = set()
    for item in repo.index.diff(None):
        if item.a_path:
            paths.add(item.a_path)
        if item.b_path:
            paths.add(item.b_path)
    for path in repo.untracked_files:
        if path:
            paths.add(path)
    return sorted(paths)


def _load_metadata(metadata_path: Optional[str], manifest_path: Optional[str]) -> dict:
    """Locate ``<id>.metadata.json`` from either the explicit column or the manifest sibling."""
    if metadata_path and os.path.exists(metadata_path):
        path = metadata_path
    elif manifest_path and manifest_path.endswith(".manifest.json"):
        path = manifest_path[: -len(".manifest.json")] + ".metadata.json"
    else:
        path = None
    if not path or not os.path.exists(path):
        raise click.ClickException(
            f"Snapshot metadata not found (looked for {path or '<unknown>'})."
        )
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Could not read snapshot metadata at {path}: {exc}")


def _load_manifest(manifest_path: Optional[str]) -> list:
    if not manifest_path or not os.path.exists(manifest_path):
        raise click.ClickException(
            f"Snapshot manifest not found at {manifest_path!r}."
        )
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            return json.load(f) or []
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"Could not read manifest at {manifest_path}: {exc}")


@click.command(
    "restore",
    help=(
        "Restore the worktree to the exact state of <snapshot_id> "
        "(strict sync: extra files are removed). Use --dry-run to preview "
        "and --force to allow restore over a dirty worktree."
    ),
)
@click.argument("snapshot_id", required=True)
@click.option(
    "--config",
    "config_path",
    default=None,
    help="Path to .ailine.yml (defaults to AILINE_POLICY_PATH or ./.ailine.yml).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the write/delete plan and exit without touching the worktree.",
)
@click.option(
    "--force",
    is_flag=True,
    help="Allow restore over a dirty working tree (uncommitted changes will be overwritten).",
)
def restore_command(
    snapshot_id: str, config_path: Optional[str], dry_run: bool, force: bool
) -> None:
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

    storage_dir = resolve_storage_dir(config.snapshot, git_root)

    row = repository.fetch_snapshot_restore_row(snapshot_id)
    if not row:
        raise click.ClickException(
            f"Snapshot not found: {snapshot_id}. "
            "Use `ailine status` to list available snapshot IDs."
        )

    metadata = _load_metadata(row["metadata_path"], row["manifest_path"])
    if metadata.get("format") != SNAPSHOT_FORMAT_OBJECTS_V1:
        raise click.ClickException(
            f"Snapshot {snapshot_id} is not in {SNAPSHOT_FORMAT_OBJECTS_V1!r} format "
            f"(got {metadata.get('format')!r}). "
            "Run `ailine prune-legacy-snapshots` to remove legacy rows."
        )

    manifest_entries = _load_manifest(row["manifest_path"])

    ignore_spec = load_ignore_spec(git_root)
    try:
        plan = plan_restore(
            manifest_entries=manifest_entries,
            storage_dir=storage_dir,
            repo_root=git_root,
            preserve_spec=ignore_spec,
        )
    except ValueError as exc:
        raise click.ClickException(f"Snapshot manifest rejected: {exc}") from exc

    repo = git.Repo(git_root)
    if repo.is_dirty(untracked_files=True) and not force:
        dirty_paths = _all_dirty_paths(repo)
        mutation_paths = {e.rel_path for e in plan.writes} | set(plan.deletions)
        blocking = [p for p in dirty_paths if p in mutation_paths]
        if blocking:
            click.echo("error: dirty paths that restore would modify:", err=True)
            for rel in blocking:
                click.echo(f"  - {rel}", err=True)
            raise click.ClickException(
                "Working tree is dirty. Commit/stash your changes, or rerun with "
                "--force to overwrite. Use --dry-run first to preview the plan."
            )

    if plan.missing_objects:
        click.echo(
            f"error: {len(plan.missing_objects)} object(s) missing from {storage_dir}:",
            err=True,
        )
        for entry in plan.missing_objects[:5]:
            click.echo(f"  missing: {entry.rel_path} (sha256={entry.sha256})", err=True)
        raise click.ClickException("Restore aborted: incomplete object store.")

    _print_plan(plan, snapshot_id, dry_run, len(PROTECTED_DIR_NAMES))

    if dry_run:
        return

    apply_restore(plan, storage_dir=storage_dir, repo_root=git_root)
    click.echo(
        f"ailine restore: snapshot={snapshot_id} "
        f"wrote={len(plan.writes)} deleted={len(plan.deletions)}",
        err=True,
    )


def _print_plan(plan, snapshot_id: str, dry_run: bool, _protected_count: int) -> None:
    label = "would-write" if dry_run else "write"
    delete_label = "would-delete" if dry_run else "delete"
    click.echo(
        f"ailine restore: snapshot={snapshot_id} writes={len(plan.writes)} "
        f"deletions={len(plan.deletions)} skipped_pointers={len(plan.skipped_pointer_paths)}",
        err=True,
    )
    sample_writes = plan.writes[:5]
    for entry in sample_writes:
        click.echo(f"  {label}: {entry.rel_path}", err=True)
    if len(plan.writes) > len(sample_writes):
        click.echo(f"  ... and {len(plan.writes) - len(sample_writes)} more", err=True)
    sample_deletions = plan.deletions[:5]
    for rel in sample_deletions:
        click.echo(f"  {delete_label}: {rel}", err=True)
    if len(plan.deletions) > len(sample_deletions):
        click.echo(f"  ... and {len(plan.deletions) - len(sample_deletions)} more", err=True)
    for rel in plan.skipped_pointer_paths[:5]:
        click.echo(
            f"  skip: {rel} (large/DVC pointer not restorable in v1)", err=True
        )
