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

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_commit_view_uses_read_only_git_object_access(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = [SimpleNamespace(hexsha="abc1234full")]
        mock_repo.git.ls_tree.return_value = "train.py\n.hidden.txt\nnotes/readme.md\n"

        def _show(spec):
            if spec.endswith("notes/readme.md"):
                return "readme"
            if spec.endswith("train.py"):
                return "print('x')"
            raise RuntimeError("should not happen")

        mock_repo.git.show.side_effect = _show
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234")
        self.assertEqual(response.status_code, 200)
        mock_repo.git.ls_tree.assert_called_once()
        self.assertEqual(mock_repo.git.show.call_count, 2)
        mock_repo.git.clean.assert_not_called()
        mock_repo.git.reset.assert_not_called()
        mock_repo.git.checkout.assert_not_called()

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_commit_view_handles_unreadable_files(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = [SimpleNamespace(hexsha="abc1234full")]
        mock_repo.git.ls_tree.return_value = "binary.bin\n"
        mock_repo.git.show.side_effect = RuntimeError("binary")
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234")
        self.assertEqual(response.status_code, 200)
        args, kwargs = mock_render.call_args
        files = kwargs["files"]
        self.assertEqual(files[0]["path"], "binary.bin")
        self.assertIn("Binary or unreadable file", files[0]["content"])

    @patch("ailine.web.routes.commit_view.render_template")
    @patch("ailine.web.routes.commit_view.git.Repo")
    def test_commit_view_sanitizes_invalid_utf8_surrogates(self, mock_repo_cls, mock_render):
        mock_render.return_value = "ok"
        mock_repo = MagicMock()
        mock_repo.iter_commits.return_value = [SimpleNamespace(hexsha="abc1234full")]
        mock_repo.git.ls_tree.return_value = "strange.txt\n"
        mock_repo.git.show.return_value = "bad-surrogate-\udccb"
        mock_repo_cls.return_value = mock_repo

        response = self.client.get("/commit/abc1234")
        self.assertEqual(response.status_code, 200)
        _, kwargs = mock_render.call_args
        files = kwargs["files"]
        self.assertEqual(files[0]["path"], "strange.txt")
        self.assertIn("bad-surrogate-", files[0]["content"])


if __name__ == "__main__":
    unittest.main()
