"""tar.zst archive creation/extraction and on-disk snapshot bundle layout."""

import hashlib
import json
import logging
import os
import tarfile
import tempfile
from datetime import datetime
from typing import List

import yaml
import zstandard as zstd

from ailine.config import constants
from ailine.snapshot.paths import sha256_file


def create_snapshot_metafile(snapshot_hash: str, parent_commit_hash: str) -> None:
    data = {"parent_commit_hash": parent_commit_hash, "hash": snapshot_hash}
    with open(".meta.yaml", "w", encoding="utf-8") as meta_file:
        yaml.dump(data, meta_file, default_flow_style=False)


def create_tar_zst_archive(snapshot_base: str, repo_path: str, archive_entries: List[dict]) -> str:
    archive_path = f"{snapshot_base}.tar.zst"
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp_tar:
        temp_tar_path = tmp_tar.name
    try:
        with tarfile.open(temp_tar_path, mode="w") as tar:
            for entry in sorted(archive_entries, key=lambda item: item["rel_path"]):
                tar.add(entry["full_path"], arcname=entry["rel_path"], recursive=False)
        cctx = zstd.ZstdCompressor(level=10)
        with open(temp_tar_path, "rb") as src, open(archive_path, "wb") as dst:
            cctx.copy_stream(src, dst)
    finally:
        if os.path.exists(temp_tar_path):
            os.remove(temp_tar_path)
    return archive_path


def extract_tar_zst_archive(archive_path: str, output_dir: str) -> None:
    dctx = zstd.ZstdDecompressor()
    with open(archive_path, "rb") as src:
        with dctx.stream_reader(src) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                tar.extractall(output_dir)


def create_snapshot(
    manifest_entries: List[dict],
    archive_entries: List[dict],
    parent_commit_hash: str,
    storage_dir: str,
    diff_text: str,
    untracked_files: List[str],
) -> dict:
    manifest_json = json.dumps(manifest_entries, sort_keys=True, separators=(",", ":"))
    snapshot_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    snapshot_dir = os.path.abspath(storage_dir)
    snapshot_base = os.path.join(snapshot_dir, snapshot_hash)
    os.makedirs(snapshot_dir, exist_ok=True)

    original_dir = os.getcwd()
    os.chdir(constants.REPO_DIR)
    try:
        create_snapshot_metafile(snapshot_hash, parent_commit_hash)
        snapshot_path = create_tar_zst_archive(snapshot_base, os.getcwd(), archive_entries)
    finally:
        os.chdir(original_dir)

    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"Snapshot not created at {snapshot_path}")

    manifest_path = f"{snapshot_base}.manifest.json"
    metadata_path = f"{snapshot_base}.metadata.json"
    diff_path = f"{snapshot_base}.diff.patch"
    archive_sha256 = sha256_file(snapshot_path)

    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest_entries, manifest_file, indent=2, sort_keys=True)
    with open(diff_path, "w", encoding="utf-8") as diff_file:
        diff_file.write(diff_text)

    metadata = {
        "snapshot_id": snapshot_hash,
        "parent_commit": parent_commit_hash,
        "created_at": datetime.now().isoformat(),
        "archive_path": snapshot_path,
        "archive_sha256": archive_sha256,
        "archive_bytes": os.path.getsize(snapshot_path),
        "manifest_path": manifest_path,
        "diff_path": diff_path,
        "untracked_files": untracked_files,
    }
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2, sort_keys=True)

    logging.info(f"Snapshot created: {snapshot_path}")
    return {
        "snapshot_hash": snapshot_hash,
        "snapshot_path": snapshot_path,
        "manifest_path": manifest_path,
        "metadata_path": metadata_path,
        "archive_bytes": os.path.getsize(snapshot_path),
        "diff_path": diff_path,
    }
