import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ailine
from ailine.config import constants


class SnapshotViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "test.db")
        ailine.init_db()

        self.snapshot_path = os.path.join(self.tmp.name, "snap.tar.zst")
        with open(self.snapshot_path, "wb") as f:
            f.write(b"placeholder")
        self.manifest_path = os.path.join(self.tmp.name, "snap.manifest.json")
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {"path": "train.py", "decision": "include", "classification": "include"},
                    {"path": "notes/readme.md", "decision": "include", "classification": "include"},
                    {"path": "big.bin", "decision": "skip", "classification": "large-non-dvc"},
                    {"path": ".hidden", "decision": "include", "classification": "include"},
                ],
                f,
            )
        self.diff_path = os.path.join(self.tmp.name, "snap.diff.patch")
        with open(self.diff_path, "w", encoding="utf-8") as f:
            f.write("diff --git a/train.py b/train.py\n@@ -1 +1 @@\n-old\n+new\n")

        conn = sqlite3.connect(constants.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tree (id, type, parent, snapshot_path, manifest_path, diff_path, "
            "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                "snap1234",
                "snapshot",
                "parent1",
                self.snapshot_path,
                self.manifest_path,
                self.diff_path,
                "2026-05-03T07:00:00",
            ),
        )
        conn.commit()
        conn.close()
        self.client = ailine.app.test_client()

    def tearDown(self):
        constants.DB_PATH = self.old_db_path

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_index_builds_tree_without_extracting_archive(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get("/snapshot/snap1234")
            self.assertEqual(response.status_code, 200)
            mock_extract.assert_not_called()
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["paths"], ["notes/readme.md", "train.py"])
        self.assertIsNone(kwargs["blob"])
        self.assertEqual(kwargs["view_mode"], "files")

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_diff_view_renders_patch(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get("/snapshot/snap1234?view=diff")
            self.assertEqual(response.status_code, 200)
            mock_extract.assert_not_called()
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["view_mode"], "diff")
        self.assertTrue(kwargs["diff"]["available"])
        self.assertIn("+new", kwargs["diff"]["text"])
        self.assertTrue(kwargs["diff"]["sections"])
        self.assertEqual(kwargs["diff"]["sections"][0]["title"], "train.py")

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_path_traversal_rejected(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get("/snapshot/snap1234?path=../etc/passwd")
            self.assertEqual(response.status_code, 404)
            mock_extract.assert_not_called()

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_unknown_path_rejected(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get("/snapshot/snap1234?path=missing.py")
            self.assertEqual(response.status_code, 404)
            mock_extract.assert_not_called()

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_blob_extraction_reads_one_file(self, mock_render):
        mock_render.return_value = "ok"

        def fake_extract(archive_path, output_dir):
            os.makedirs(os.path.join(output_dir, "notes"), exist_ok=True)
            with open(os.path.join(output_dir, "train.py"), "w", encoding="utf-8") as f:
                f.write("print('x')\n")
            with open(os.path.join(output_dir, "notes", "readme.md"), "w", encoding="utf-8") as f:
                f.write("# notes\n")

        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive",
            side_effect=fake_extract,
        ) as mock_extract:
            response = self.client.get("/snapshot/snap1234?path=train.py")
            self.assertEqual(response.status_code, 200)
            mock_extract.assert_called_once()
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["blob"]["path"], "train.py")
        self.assertIn("print", kwargs["blob"]["content"])
        self.assertEqual(kwargs["blob"]["language"], "python")


if __name__ == "__main__":
    unittest.main()
