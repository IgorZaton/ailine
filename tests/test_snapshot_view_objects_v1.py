"""Tests for the objects-v1 read path of the ``/snapshot/<id>`` route."""

import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import ailine
from ailine.config import constants
from ailine.snapshot import object_store


def _write_object(storage: str, content: bytes) -> str:
    sha = hashlib.sha256(content).hexdigest()
    src_dir = os.path.join(storage, "_src")
    os.makedirs(src_dir, exist_ok=True)
    src_path = os.path.join(src_dir, sha)
    with open(src_path, "wb") as f:
        f.write(content)
    object_store.put_file(src_path, sha, storage)
    os.remove(src_path)
    return sha


class ObjectsV1ViewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "test.db")
        ailine.init_db()

        self.storage = os.path.join(self.tmp.name, "snapshots")
        os.makedirs(self.storage, exist_ok=True)

        self.train_sha = _write_object(self.storage, b"print('x')\n")
        self.readme_sha = _write_object(self.storage, b"# notes\n")
        self.binary_sha = _write_object(self.storage, b"\x00\xff\x00\xff\x00\xff")

        snap_id = "snapobj1"
        self.manifest_path = os.path.join(self.storage, f"{snap_id}.manifest.json")
        with open(self.manifest_path, "w", encoding="utf-8") as f:
            json.dump(
                [
                    {
                        "path": "train.py",
                        "decision": "include",
                        "classification": "include",
                        "sha256": self.train_sha,
                    },
                    {
                        "path": "notes/readme.md",
                        "decision": "include",
                        "classification": "include",
                        "sha256": self.readme_sha,
                    },
                    {
                        "path": "blob.bin",
                        "decision": "include",
                        "classification": "include",
                        "sha256": self.binary_sha,
                    },
                ],
                f,
            )
        self.metadata_path = os.path.join(self.storage, f"{snap_id}.metadata.json")
        with open(self.metadata_path, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "format": "objects-v1",
                    "objects_dir": os.path.join(self.storage, "objects"),
                    "archive_path": None,
                },
                f,
            )
        self.diff_path = os.path.join(self.storage, f"{snap_id}.diff.patch")
        with open(self.diff_path, "w", encoding="utf-8") as f:
            f.write("")

        conn = sqlite3.connect(constants.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tree (id, type, parent, snapshot_path, manifest_path, diff_path, "
            "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                snap_id,
                "snapshot",
                "parent1",
                None,
                self.manifest_path,
                self.diff_path,
                "2026-05-04T07:00:00",
            ),
        )
        conn.commit()
        conn.close()
        self.snap_id = snap_id
        self.client = ailine.app.test_client()

    def tearDown(self):
        constants.DB_PATH = self.old_db_path

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_index_builds_tree_without_extracting(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get(f"/snapshot/{self.snap_id}")
            self.assertEqual(response.status_code, 200)
            mock_extract.assert_not_called()
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["paths"], ["blob.bin", "notes/readme.md", "train.py"])

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_blob_read_via_object_store(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get(
                f"/snapshot/{self.snap_id}?path=train.py"
            )
            self.assertEqual(response.status_code, 200)
            mock_extract.assert_not_called()
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["blob"]["path"], "train.py")
        self.assertIn("print", kwargs["blob"]["content"])
        self.assertEqual(kwargs["blob"]["language"], "python")

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_binary_blob_returns_unreadable_marker(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get(
                f"/snapshot/{self.snap_id}?path=blob.bin"
            )
            self.assertEqual(response.status_code, 200)
            mock_extract.assert_not_called()
        _, kwargs = mock_render.call_args
        self.assertIn("Binary or unreadable", kwargs["blob"]["content"])

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_unknown_path_rejected(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get(
                f"/snapshot/{self.snap_id}?path=missing.py"
            )
            self.assertEqual(response.status_code, 404)
            mock_extract.assert_not_called()

    @patch("ailine.web.routes.snapshot_view.render_template")
    def test_path_traversal_rejected(self, mock_render):
        mock_render.return_value = "ok"
        with patch(
            "ailine.web.routes.snapshot_view.extract_tar_zst_archive"
        ) as mock_extract:
            response = self.client.get(
                f"/snapshot/{self.snap_id}?path=../etc/passwd"
            )
            self.assertEqual(response.status_code, 404)
            mock_extract.assert_not_called()


if __name__ == "__main__":
    unittest.main()
