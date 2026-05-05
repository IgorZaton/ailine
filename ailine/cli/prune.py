"""``ailine prune-legacy-snapshots``: one-shot cleanup for pre-objects-v1 rows.

A snapshot row is considered legacy when any of the following holds:

* ``manifest_path`` is unset or the file is missing on disk;
* the sibling ``<base>.metadata.json`` is missing or unparsable;
* the parsed metadata has no ``format`` field, or ``format != 'objects-v1'``;
* the ``snapshot_path`` column points at a ``*.tar.zst`` archive (the old
  per-snapshot tar payload).

For each match we delete the row and any orphan
``<base>.{manifest.json, metadata.json, diff.patch, tar.zst}`` files. With
``--dry-run`` nothing is touched; the would-be-affected rows/files are just
listed.
"""

from __future__ import annotations

import json
import logging
import os
from typing import List, Optional, Tuple

import click

from ailine.persistence import repository
from ailine.snapshot.archive import SNAPSHOT_FORMAT_OBJECTS_V1


_SIBLING_SUFFIXES = (
    ".manifest.json",
    ".metadata.json",
    ".diff.patch",
    ".tar.zst",
)


def _metadata_sibling(manifest_path: Optional[str]) -> Optional[str]:
    if not manifest_path or not manifest_path.endswith(".manifest.json"):
        return None
    return manifest_path[: -len(".manifest.json")] + ".metadata.json"


def _is_legacy_row(row: dict) -> Tuple[bool, str]:
    """Return ``(is_legacy, reason)`` for one snapshot row."""
    snapshot_path = row.get("snapshot_path") or ""
    if snapshot_path.endswith(".tar.zst"):
        return True, "snapshot_path points at a .tar.zst payload"

    manifest_path = row.get("manifest_path")
    if not manifest_path or not os.path.exists(manifest_path):
        return True, "manifest file missing"

    meta_path = _metadata_sibling(manifest_path)
    if not meta_path or not os.path.exists(meta_path):
        return True, "metadata sibling missing"

    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f) or {}
    except (OSError, ValueError):
        return True, "metadata sibling unparsable"

    fmt = meta.get("format")
    if fmt != SNAPSHOT_FORMAT_OBJECTS_V1:
        return True, f"metadata.format={fmt!r} (not objects-v1)"
    return False, ""


def _orphan_sibling_paths(manifest_path: Optional[str], snapshot_path: Optional[str]) -> List[str]:
    """Compute likely orphan files for a legacy row."""
    bases: set[str] = set()
    if manifest_path and manifest_path.endswith(".manifest.json"):
        bases.add(manifest_path[: -len(".manifest.json")])
    if snapshot_path and snapshot_path.endswith(".tar.zst"):
        bases.add(snapshot_path[: -len(".tar.zst")])
    paths: List[str] = []
    for base in bases:
        for suffix in _SIBLING_SUFFIXES:
            candidate = f"{base}{suffix}"
            if os.path.exists(candidate):
                paths.append(candidate)
    return paths


@click.command(
    "prune-legacy-snapshots",
    help=(
        "Remove pre-objects-v1 snapshot rows from the lineage DB and their "
        "orphan .manifest/.metadata/.diff/.tar.zst files. Use --dry-run to "
        "preview without touching anything."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print what would be removed and exit without touching the DB or filesystem.",
)
def prune_legacy_snapshots_command(dry_run: bool) -> None:
    rows = repository.fetch_all_snapshot_locations()
    pruned_rows = 0
    removed_files = 0

    for row in rows:
        is_legacy, reason = _is_legacy_row(row)
        if not is_legacy:
            continue

        sibling_paths = _orphan_sibling_paths(row.get("manifest_path"), row.get("snapshot_path"))
        verb = "would prune" if dry_run else "pruning"
        click.echo(
            f"{verb}: id={row['id']} parent={row.get('parent') or '-'} reason={reason}"
        )
        for path in sibling_paths:
            sub_verb = "would remove" if dry_run else "remove"
            click.echo(f"  {sub_verb}: {path}")

        if dry_run:
            pruned_rows += 1
            removed_files += len(sibling_paths)
            continue

        for path in sibling_paths:
            try:
                os.remove(path)
                removed_files += 1
            except OSError as exc:
                logging.warning("Could not delete %s: %s", path, exc)

        repository.delete_run(row["id"])
        pruned_rows += 1

    summary_prefix = "dry-run summary" if dry_run else "summary"
    click.echo(f"{summary_prefix}: pruned {pruned_rows} rows, removed {removed_files} files")
