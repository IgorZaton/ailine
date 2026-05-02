import json
import os
import tempfile
import unittest
from unittest.mock import patch

import ailine


class SnapshotStage1Tests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.repo_dir = os.path.join(self.temp_dir.name, "repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        self.original_repo_dir = ailine.REPO_DIR
        self.original_state_dir = ailine.STATE_DIR
        self.original_policy_store = ailine.LARGE_FILE_POLICY_STORE
        self.original_object_store = ailine.OBJECT_STORE_DIR
        self.original_pointer_store = ailine.POINTER_STORE_DIR
        ailine.REPO_DIR = self.repo_dir
        ailine.STATE_DIR = os.path.join(self.temp_dir.name, ".ailine")
        ailine.LARGE_FILE_POLICY_STORE = os.path.join(ailine.STATE_DIR, "large-file-policy.json")
        ailine.OBJECT_STORE_DIR = os.path.join(ailine.STATE_DIR, "objects")
        ailine.POINTER_STORE_DIR = os.path.join(ailine.STATE_DIR, "pointers")

    def tearDown(self):
        ailine.REPO_DIR = self.original_repo_dir
        ailine.STATE_DIR = self.original_state_dir
        ailine.LARGE_FILE_POLICY_STORE = self.original_policy_store
        ailine.OBJECT_STORE_DIR = self.original_object_store
        ailine.POINTER_STORE_DIR = self.original_pointer_store

    def _write_file(self, rel_path: str, content: bytes):
        full_path = os.path.join(self.repo_dir, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "wb") as f:
            f.write(content)

    def test_manifest_snapshot_id_is_deterministic(self):
        self._write_file("train.py", b"print('x')\n")
        policy = {
            "exclude_globs": [".git/**"],
            "large_file_mb": 10,
            "large_file_mode": "prompt",
            "dvc_pointer_patterns": ["*.dvc"],
        }
        entries = ailine.scan_repo_files(self.repo_dir, policy)
        m1, _, extra1 = ailine.build_manifest(entries, self.temp_dir.name)
        m2, _, extra2 = ailine.build_manifest(entries, self.temp_dir.name)
        self.assertEqual(extra1["summary"]["snapshot_id"], extra2["summary"]["snapshot_id"])
        self.assertEqual(m1, m2)

    def test_remembered_large_file_decision_auto_applies(self):
        self._write_file("big.bin", b"x" * 16)
        policy = {
            "exclude_globs": [".git/**"],
            "large_file_mb": 0.000001,
            "large_file_mode": "prompt",
            "dvc_pointer_patterns": ["*.dvc"],
        }
        entries = ailine.scan_repo_files(self.repo_dir, policy)
        with patch("click.prompt", return_value="include"), patch("click.confirm", return_value=True):
            decided, _ = ailine.resolve_large_file_decisions(entries, policy)
        self.assertEqual(decided[0]["decision"], "include")

        entries_again = ailine.scan_repo_files(self.repo_dir, policy)
        with patch("click.prompt", side_effect=AssertionError("should not prompt")):
            decided_again, _ = ailine.resolve_large_file_decisions(entries_again, policy)
        self.assertEqual(decided_again[0]["decision"], "include")
        self.assertEqual(decided_again[0]["decision_source"], "memory")

    def test_large_file_pointer_is_deduplicated(self):
        self._write_file("big.bin", b"large-content")
        policy = {
            "exclude_globs": [".git/**"],
            "large_file_mb": 0.000001,
            "large_file_mode": "prompt",
            "dvc_pointer_patterns": ["*.dvc"],
        }
        entries = ailine.scan_repo_files(self.repo_dir, policy)
        with patch("click.prompt", return_value="include"), patch("click.confirm", return_value=False):
            decided, _ = ailine.resolve_large_file_decisions(entries, policy)
        _, _, extra_1 = ailine.build_manifest(decided, self.temp_dir.name)
        _, _, extra_2 = ailine.build_manifest(decided, self.temp_dir.name)
        self.assertEqual(extra_1["summary"]["large_file_pointer_count"], 1)
        self.assertEqual(extra_2["summary"]["large_file_pointer_count"], 1)

        object_files = os.listdir(ailine.OBJECT_STORE_DIR)
        self.assertEqual(len(object_files), 1)
        pointer_files = os.listdir(ailine.POINTER_STORE_DIR)
        self.assertEqual(len(pointer_files), 1)
        with open(os.path.join(ailine.POINTER_STORE_DIR, pointer_files[0]), "r", encoding="utf-8") as f:
            pointer_payload = json.load(f)
        self.assertEqual(pointer_payload["path"], "big.bin")

    def test_excluded_cache_paths_not_tracked(self):
        self._write_file("__pycache__/module.cpython-312.pyc", b"compiled")
        self._write_file("train.py", b"print('ok')\n")
        policy = {
            "exclude_globs": [".git/**", "__pycache__/**"],
            "large_file_mb": 10,
            "large_file_mode": "prompt",
            "dvc_pointer_patterns": ["*.dvc"],
        }
        entries = ailine.scan_repo_files(self.repo_dir, policy)
        rel_paths = [entry["rel_path"] for entry in entries]
        self.assertIn("train.py", rel_paths)
        self.assertNotIn("__pycache__/module.cpython-312.pyc", rel_paths)


if __name__ == "__main__":
    unittest.main()
