"""Snapshot bundle creation in the ``objects-v1`` layout.

Each included file is written once to a content-addressed object store at
``<storage_dir>/objects/<sha[:2]>/<sha>.zst`` and the manifest entries
already carry the same ``sha256`` keys. Two snapshots that share a file
share the underlying object on disk.

The demo-only ``.meta.yaml`` hook (``write_meta_file=True``) is unrelated to
the bundle format and is preserved for the ``ailine run`` flow.
"""

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import List

import yaml

from ailine.config import constants
from ailine.snapshot import object_store


def create_snapshot_metafile(snapshot_hash: str, parent_commit_hash: str) -> None:
    data = {"parent_commit_hash": parent_commit_hash, "hash": snapshot_hash}
    with open(".meta.yaml", "w", encoding="utf-8") as meta_file:
        yaml.dump(data, meta_file, default_flow_style=False)


SNAPSHOT_FORMAT_OBJECTS_V1 = "objects-v1"


def _write_objects(archive_entries: List[dict], storage_dir: str) -> dict:
    """Persist included files as content-addressed objects.

    Returns a small summary dict; ``object_bytes_total`` reflects the
    *uncompressed* sum of stored file sizes (kept compatible with the
    ``archive_bytes`` field consumers already read).
    """
    seen: set[str] = set()
    object_bytes_total = 0
    for entry in archive_entries:
        sha = entry["sha256"]
        full_path = entry["full_path"]
        object_store.put_file(full_path, sha, storage_dir)
        if sha not in seen:
            seen.add(sha)
            object_bytes_total += int(entry.get("size", os.path.getsize(full_path)))
    return {
        "object_count": len(seen),
        "object_bytes_total": object_bytes_total,
    }


def create_snapshot(
    manifest_entries: List[dict],
    archive_entries: List[dict],
    parent_commit_hash: str,
    storage_dir: str,
    diff_text: str,
    untracked_files: List[str],
    repo_path: str = None,
    write_meta_file: bool = True,
) -> dict:
    """Build a snapshot bundle on disk in the ``objects-v1`` layout.

    ``repo_path`` defaults to :data:`constants.REPO_DIR` for backward compat
    with ``ailine run`` (demo flow). Pass an explicit path (e.g. resolved git
    root) when called from ``ailine track``. ``write_meta_file=False`` skips
    writing the demo-only ``.meta.yaml`` placeholder into the user's tree.

    Side effects:
        * Writes one zstd-compressed object per unique included file under
          ``<storage_dir>/objects/<sha[:2]>/<sha>.zst`` (idempotent / shared
          across snapshots).
        * Writes ``<storage_dir>/<id>.manifest.json``,
          ``<storage_dir>/<id>.metadata.json``, ``<storage_dir>/<id>.diff.patch``.
    """
    manifest_json = json.dumps(manifest_entries, sort_keys=True, separators=(",", ":"))
    snapshot_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    snapshot_dir = os.path.abspath(storage_dir)
    snapshot_base = os.path.join(snapshot_dir, snapshot_hash)
    os.makedirs(snapshot_dir, exist_ok=True)

    repo_root = repo_path or constants.REPO_DIR
    original_dir = os.getcwd()
    os.chdir(repo_root)
    try:
        if write_meta_file:
            create_snapshot_metafile(snapshot_hash, parent_commit_hash)
        objects_summary = _write_objects(archive_entries, snapshot_dir)
    finally:
        os.chdir(original_dir)

    manifest_path = f"{snapshot_base}.manifest.json"
    metadata_path = f"{snapshot_base}.metadata.json"
    diff_path = f"{snapshot_base}.diff.patch"
    objects_dir = os.path.join(snapshot_dir, "objects")

    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest_entries, manifest_file, indent=2, sort_keys=True)
    with open(diff_path, "w", encoding="utf-8") as diff_file:
        diff_file.write(diff_text)

    metadata = {
        "snapshot_id": snapshot_hash,
        "parent_commit": parent_commit_hash,
        "created_at": datetime.now().isoformat(),
        "format": SNAPSHOT_FORMAT_OBJECTS_V1,
        "objects_dir": objects_dir,
        "object_count": objects_summary["object_count"],
        "archive_bytes": objects_summary["object_bytes_total"],
        "manifest_path": manifest_path,
        "diff_path": diff_path,
        "untracked_files": untracked_files,
    }
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2, sort_keys=True)

    logging.info(
        "Snapshot created (objects-v1): id=%s objects=%d objects_dir=%s",
        snapshot_hash,
        objects_summary["object_count"],
        objects_dir,
    )
    return {
        "snapshot_hash": snapshot_hash,
        "snapshot_path": None,
        "manifest_path": manifest_path,
        "metadata_path": metadata_path,
        "archive_bytes": objects_summary["object_bytes_total"],
        "diff_path": diff_path,
    }
