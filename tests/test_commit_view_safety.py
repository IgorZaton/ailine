import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import ailine
from ailine.config import constants


class CommitViewSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "test.db")
        ailine.init_db()
        conn = sqlite3.connect(constants.DB_PATH)
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tree (id, git_url) VALUES (?, ?)",
            ("abc1234", "https://example.com/commit/abc1234"),
        )
        conn.commit()
        conn.close()
        self.client = ailine.app.test_client()

    def tearDown(self):
        constants.DB_PATH = self.old_db_path

    def _make_repo_mock(self, paths):
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = [SimpleNamespace(hexsha="abc1234full")]
        mock_repo.git.ls_tree.return_value = "\n".join(paths) + "\n"
        return mock_repo

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_index_lists_paths_without_loading_any_blob(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = self._make_repo_mock(["train.py", ".hidden.txt", "notes/readme.md"])
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234")
        self.assertEqual(response.status_code, 200)
        mock_repo.git.ls_tree.assert_called_once()
        mock_repo.git.show.assert_not_called()
        mock_repo.git.clean.assert_not_called()
        mock_repo.git.reset.assert_not_called()
        mock_repo.git.checkout.assert_not_called()
        _, kwargs = mock_render.call_args
        self.assertIsNone(kwargs["blob"])
        self.assertEqual(kwargs["paths"], ["notes/readme.md", "train.py"])

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_path_query_loads_single_blob(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = self._make_repo_mock(["train.py", "notes/readme.md"])
        mock_repo.git.show.return_value = "print('x')"
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234?path=train.py")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_repo.git.show.call_count, 1)
        _, kwargs = mock_render.call_args
        self.assertEqual(kwargs["blob"]["path"], "train.py")
        self.assertEqual(kwargs["blob"]["language"], "python")
        self.assertFalse(kwargs["blob"]["truncated"])

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_unreadable_file_falls_back_to_message(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = self._make_repo_mock(["binary.bin"])
        mock_repo.git.show.side_effect = RuntimeError("binary")
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234?path=binary.bin")
        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_render.call_args
        self.assertIn("Binary or unreadable file", kwargs["blob"]["content"])

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_path_traversal_rejected(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = self._make_repo_mock(["train.py"])
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234?path=../../etc/passwd")
        self.assertEqual(response.status_code, 404)
        mock_repo.git.show.assert_not_called()

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_unknown_path_rejected(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = self._make_repo_mock(["train.py"])
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234?path=does_not_exist.py")
        self.assertEqual(response.status_code, 404)
        mock_repo.git.show.assert_not_called()

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_commit_view_sanitizes_invalid_utf8_surrogates(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = self._make_repo_mock(["strange.txt"])
        mock_repo.git.show.return_value = "bad-surrogate-\udccb"
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234?path=strange.txt")
        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_render.call_args
        self.assertIn("bad-surrogate-", kwargs["blob"]["content"])


if __name__ == "__main__":
    unittest.main()
