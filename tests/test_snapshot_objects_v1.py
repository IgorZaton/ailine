"""Tests for the objects-v1 snapshot bundle layout."""

import glob
import json
import os
import tempfile
import unittest

from ailine.snapshot.archive import SNAPSHOT_FORMAT_OBJECTS_V1, create_snapshot
from ailine.snapshot.manifest import build_manifest
from ailine.snapshot.scan import resolve_large_file_decisions, scan_repo_files


_SNAPSHOT_POLICY = {
    "exclude_globs": [".git/**"],
    "large_file_mb": 10,
    "large_file_mode": "include",
    "dvc_pointer_patterns": ["*.dvc"],
}


def _write(repo: str, rel: str, content: bytes) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "wb") as f:
        f.write(content)


def _make_snapshot(repo: str, storage: str) -> dict:
    entries = scan_repo_files(repo, _SNAPSHOT_POLICY)
    entries, _ = resolve_large_file_decisions(entries, _SNAPSHOT_POLICY)
    manifest_entries, archive_entries, _ = build_manifest(entries, storage)
    return create_snapshot(
        manifest_entries=manifest_entries,
        archive_entries=archive_entries,
        parent_commit_hash="0" * 40,
        storage_dir=storage,
        diff_text="",
        untracked_files=[],
        repo_path=repo,
        write_meta_file=False,
    )


class ObjectsV1SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.storage = os.path.join(self.tmp.name, "storage")
        os.makedirs(self.repo, exist_ok=True)

    def test_create_snapshot_writes_objects_only(self):
        _write(self.repo, "train.py", b"print('x')\n")
        _write(self.repo, "data/util.py", b"def f():\n    return 1\n")

        result = _make_snapshot(self.repo, self.storage)

        self.assertIsNone(result["snapshot_path"])
        tar_files = glob.glob(os.path.join(self.storage, "*.tar.zst"))
        self.assertEqual(tar_files, [], msg=f"unexpected tar payload(s): {tar_files}")

        manifest_files = glob.glob(os.path.join(self.storage, "*.manifest.json"))
        metadata_files = glob.glob(os.path.join(self.storage, "*.metadata.json"))
        diff_files = glob.glob(os.path.join(self.storage, "*.diff.patch"))
        self.assertEqual(len(manifest_files), 1)
        self.assertEqual(len(metadata_files), 1)
        self.assertEqual(len(diff_files), 1)

        objects_root = os.path.join(self.storage, "objects")
        self.assertTrue(os.path.isdir(objects_root), "objects dir must exist")
        stored = []
        for shard in os.listdir(objects_root):
            for name in os.listdir(os.path.join(objects_root, shard)):
                stored.append(name)
        self.assertEqual(len(stored), 2)

    def test_metadata_has_objects_v1_format(self):
        _write(self.repo, "train.py", b"print('x')\n")
        _make_snapshot(self.repo, self.storage)

        metadata_files = glob.glob(os.path.join(self.storage, "*.metadata.json"))
        with open(metadata_files[0], "r", encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["format"], SNAPSHOT_FORMAT_OBJECTS_V1)
        self.assertIsNone(meta["archive_path"])
        self.assertIsNone(meta["archive_sha256"])
        self.assertEqual(
            os.path.abspath(meta["objects_dir"]),
            os.path.abspath(os.path.join(self.storage, "objects")),
        )

    def test_two_snapshots_share_unchanged_objects(self):
        _write(self.repo, "train.py", b"print('x')\n")
        _write(self.repo, "data/util.py", b"def f():\n    return 1\n")
        first = _make_snapshot(self.repo, self.storage)

        # Modify a single file; the other one should keep the same object.
        _write(self.repo, "train.py", b"print('y')\n")
        second = _make_snapshot(self.repo, self.storage)
        self.assertNotEqual(first["snapshot_hash"], second["snapshot_hash"])

        objects_root = os.path.join(self.storage, "objects")
        stored = []
        for shard in os.listdir(objects_root):
            for name in os.listdir(os.path.join(objects_root, shard)):
                stored.append(name)
        # 2 unique files in snap1 + 1 new file in snap2 (train.py) = 3 objects total
        self.assertEqual(len(stored), 3, msg=f"expected dedup, got {stored}")

    def test_manifest_keeps_sha256_per_entry(self):
        _write(self.repo, "train.py", b"print('x')\n")
        _make_snapshot(self.repo, self.storage)

        manifest_path = glob.glob(os.path.join(self.storage, "*.manifest.json"))[0]
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
        for entry in manifest:
            self.assertIn("sha256", entry)
            self.assertEqual(len(entry["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
