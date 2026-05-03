import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ailine
from ailine.config import constants


class DvcLinkageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo_dir = os.path.join(self.tmp.name, "repo")
        os.makedirs(self.repo_dir, exist_ok=True)
        self.old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "test.db")

    def tearDown(self):
        constants.DB_PATH = self.old_db_path

    def _write_text(self, rel_path: str, content: str):
        full = os.path.join(self.repo_dir, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)

    def test_discover_dvc_outputs_from_dvc_file(self):
        self._write_text(
            "data.csv.dvc",
            "outs:\n  - path: data.csv\n    md5: abc123\n    size: 10\n",
        )
        cfg = dict(ailine.DEFAULT_DVC_CONFIG)
        outputs = ailine.discover_dvc_outputs(self.repo_dir, cfg)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0]["path"], "data.csv")
        self.assertEqual(outputs[0]["out"]["md5"], "abc123")

    @patch("ailine.linkage.dvc.subprocess.run")
    def test_build_dvc_linkage_classifies_local_only(self, mock_run):
        self._write_text(
            "data.csv.dvc",
            "outs:\n  - path: data.csv\n    md5: abc123\n    size: 10\n",
        )
        self._write_text("data.csv", "content")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        cfg = dict(ailine.DEFAULT_DVC_CONFIG)
        linkage = ailine.build_dvc_linkage(self.repo_dir, cfg)
        self.assertEqual(linkage["status"], "local_only")
        self.assertEqual(len(linkage["items"]), 1)
        self.assertEqual(linkage["items"][0]["hash_algo"], "md5")
        self.assertEqual(linkage["items"][0]["hash_value"], "abc123")
        self.assertTrue(linkage["items"][0]["is_in_cache"])

    @patch("ailine.linkage.dvc.subprocess.run")
    def test_build_dvc_linkage_remote_ready(self, mock_run):
        self._write_text(
            "data.csv.dvc",
            "outs:\n  - path: data.csv\n    md5: abc123\n    size: 10\n",
        )
        self._write_text("data.csv", "content")
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "origin s3://bucket/path\n"
        cfg = dict(ailine.DEFAULT_DVC_CONFIG)
        linkage = ailine.build_dvc_linkage(self.repo_dir, cfg)
        self.assertEqual(linkage["status"], "remote_ready")
        self.assertTrue(linkage["items"][0]["has_remote"])
        self.assertEqual(linkage["items"][0]["remote_name"], "origin")

    @patch("ailine.linkage.dvc.subprocess.run")
    def test_build_dvc_linkage_missing_when_no_cache_and_no_remote(self, mock_run):
        self._write_text(
            "data.csv.dvc",
            "outs:\n  - path: data.csv\n    md5: abc123\n    size: 10\n",
        )
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = ""
        cfg = dict(ailine.DEFAULT_DVC_CONFIG)
        linkage = ailine.build_dvc_linkage(self.repo_dir, cfg)
        self.assertEqual(linkage["status"], "missing")
        self.assertFalse(linkage["items"][0]["is_in_cache"])

    def test_db_schema_contains_dvc_columns(self):
        ailine.init_db()
        conn = sqlite3.connect(constants.DB_PATH)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(tree)")
        cols = {row[1] for row in cur.fetchall()}
        conn.close()
        self.assertIn("dvc_linkage_json", cols)
        self.assertIn("dvc_linkage_status", cols)


if __name__ == "__main__":
    unittest.main()
