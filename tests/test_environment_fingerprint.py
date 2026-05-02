import importlib.metadata
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ailine
from ailine.config import constants


class EnvironmentFingerprintTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo_root = self.tmp.name
        self.old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        constants.DB_PATH = self.old_db_path

    def test_collect_environment_fingerprint_with_lock(self):
        lock_path = os.path.join(self.repo_root, "poetry.lock")
        with open(lock_path, "w", encoding="utf-8") as f:
            f.write("lock-content")
        cfg = {"enabled": True, "packages": []}
        fingerprint, status = ailine.collect_environment_fingerprint(self.repo_root, cfg)
        self.assertEqual(status, "complete")
        self.assertIsNotNone(fingerprint["poetry_lock_sha256"])

    @patch("ailine.fingerprint.env.importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError)
    def test_missing_package_sets_partial(self, _mock_version):
        cfg = {"enabled": True, "packages": ["nonexistent-pkg"]}
        fingerprint, status = ailine.collect_environment_fingerprint(self.repo_root, cfg)
        self.assertEqual(status, "partial")
        self.assertIsNone(fingerprint["packages"]["nonexistent-pkg"])

    def test_db_schema_contains_env_columns(self):
        ailine.init_db()
        conn = sqlite3.connect(constants.DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(tree)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        self.assertIn("env_fingerprint_json", cols)
        self.assertIn("env_fingerprint_status", cols)


if __name__ == "__main__":
    unittest.main()
