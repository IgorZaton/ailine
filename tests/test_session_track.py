"""Integration tests for ``run_tracked_command`` (session orchestrator)."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ailine.config import constants
from ailine.config.validate import validate_config
from ailine.persistence.db import init_db
from ailine.run.session import SessionError, run_tracked_command


def _bootstrap_repo(tmp: str) -> str:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("hi\n")
    with open(os.path.join(repo, ".ailine.yml"), "w") as f:
        f.write("project:\n  version: 1\n  mode: track\n")
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
    return repo


class TrackedCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _bootstrap_repo(self.tmp.name)
        self.storage = os.path.join(self.tmp.name, "snapshots")

        # Isolated SQLite DB for this test.
        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)

        self.cfg_path = os.path.join(self.repo, ".ailine.yml")
        self.config = validate_config(self.cfg_path)

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def _read_run_rows(self):
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT id, type, run_command_summary, run_command_json "
                "FROM tree"
            ).fetchall()
        finally:
            conn.close()
        return rows

    def test_clean_repo_records_git_commit_and_argv(self):
        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print('ok')"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.commit_type, "git")

        rows = self._read_run_rows()
        self.assertEqual(len(rows), 1)
        rid, rtype, summary, payload_json = rows[0]
        self.assertEqual(rtype, "git")
        self.assertIn("print('ok')", summary)
        payload = json.loads(payload_json)
        self.assertEqual(payload["argv"][:2], [sys.executable, "-c"])
        self.assertEqual(payload["cwd"], self.repo)

    def test_dirty_tree_creates_snapshot(self):
        with open(os.path.join(self.repo, "new_file.txt"), "w") as f:
            f.write("uncommitted\n")

        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print(1)"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.commit_type, "snapshot")
        self.assertTrue(result.snapshot_path)
        self.assertTrue(os.path.exists(result.snapshot_path))

    def test_inherit_mode_does_not_open_outer_mlflow_run(self):
        with patch("ailine.run.session.mlflow.start_run") as start_run, patch(
            "ailine.run.session.mlflow.search_runs", return_value=None
        ):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=self.config,  # default mlflow.mode == 'inherit'
            )
            start_run.assert_not_called()

    def test_inherit_mode_records_mlflow_run_when_post_hoc_lookup_succeeds(self):
        with patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value="post-hoc-run-id",
        ) as mock_lookup:
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=self.config,
            )
        mock_lookup.assert_called_once()
        self.assertEqual(result.mlflow_run_id, "post-hoc-run-id")

    def test_wrap_mode_opens_outer_mlflow_run(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "wrap"
        with patch("ailine.run.session.mlflow.start_run") as start_run:
            ctx = start_run.return_value
            ctx.__enter__.return_value = None
            ctx.__exit__.return_value = False
            with patch(
                "ailine.run.session.mlflow.active_run"
            ) as active_run:
                active_run.return_value.info.run_id = "run-123"
                result = run_tracked_command(
                    git_root=self.repo,
                    argv=[sys.executable, "-c", "print(1)"],
                    storage=self.storage,
                    config=cfg,
                )
            start_run.assert_called_once()
            self.assertEqual(result.mlflow_run_id, "run-123")

    def test_dvc_verify_strict_aborts_on_failing_command(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["dvc"]["verify"] = "strict"
        # `false` exits 1 reliably on POSIX shells.
        cfg.track["dvc"]["verify_commands"] = [["false"]]
        with self.assertRaises(SessionError):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )

    def test_child_nonzero_exit_propagates(self):
        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "import sys; sys.exit(7)"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.exit_code, 7)

    def test_empty_argv_raises(self):
        with self.assertRaises(SessionError):
            run_tracked_command(
                git_root=self.repo,
                argv=[],
                storage=self.storage,
                config=self.config,
            )

    def test_non_executable_py_script_raises_session_error_with_hint(self):
        script_path = os.path.join(self.repo, "side.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("print('x')\n")
        subprocess.run(["git", "-C", self.repo, "add", "side.py"], check=True)
        subprocess.run(
            ["git", "-C", self.repo, "commit", "-q", "-m", "add side script"],
            check=True,
        )
        with self.assertRaises(SessionError) as ctx:
            run_tracked_command(
                git_root=self.repo,
                argv=["side.py"],
                storage=self.storage,
                config=self.config,
            )
        msg = str(ctx.exception).lower()
        self.assertIn("interpreter", msg)
        self.assertIn("python", msg)


if __name__ == "__main__":
    unittest.main()
