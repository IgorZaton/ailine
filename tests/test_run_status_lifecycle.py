"""End-to-end tests for the run status lifecycle.

Validates that ``run_tracked_command`` inserts a row with
``status = 'in_progress'`` *before* the child process runs and finalizes it
to ``done`` (zero exit) or ``failed`` (non-zero exit / AIline-side error).
"""

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
from ailine.run.session import run_tracked_command


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


def _read_status_row(db_path: str, run_id: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT status, started_at, finished_at, exit_code FROM tree WHERE id = ?",
            (run_id,),
        )
        return cur.fetchone()
    finally:
        conn.close()


class RunStatusLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _bootstrap_repo(self.tmp.name)
        self.storage = os.path.join(self.tmp.name, "snapshots")

        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)

        cfg_path = os.path.join(self.repo, ".ailine.yml")
        self.config = validate_config(cfg_path)

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def test_in_progress_row_visible_before_subprocess_completes(self):
        """While the child subprocess is running the lifecycle row already
        exists with ``status = 'in_progress'`` so the UI/CLI can show live
        runs immediately."""
        observed = {}
        last_seen = {}
        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            # ``mock.patch`` swaps ``subprocess.run`` globally; only intercept
            # the user-tracked command, not unrelated subprocess calls
            # gitpython makes during snapshot scanning.
            if cmd and cmd[0] == sys.executable and "run_id" in last_seen:
                observed["row_during_subprocess"] = _read_status_row(
                    constants.DB_PATH, last_seen["run_id"]
                )
            return real_run(*args, **kwargs)

        def capture_started(record_id, _mlflow_run_id):
            last_seen["run_id"] = record_id

        with patch("ailine.run.session.subprocess.run", side_effect=fake_run):
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print('ok')"],
                storage=self.storage,
                config=self.config,
                on_run_started=capture_started,
            )

        self.assertEqual(result.exit_code, 0)
        row_during = observed["row_during_subprocess"]
        self.assertIsNotNone(row_during)
        status_during, started_at, finished_at, exit_code = row_during
        self.assertEqual(status_during, "in_progress")
        self.assertIsNotNone(started_at)
        self.assertIsNone(finished_at)
        self.assertIsNone(exit_code)

        final_row = _read_status_row(constants.DB_PATH, last_seen["run_id"])
        self.assertIsNotNone(final_row)
        final_status, _, final_finished_at, final_exit = final_row
        self.assertEqual(final_status, "done")
        self.assertIsNotNone(final_finished_at)
        self.assertEqual(final_exit, 0)

    def test_failed_run_marks_status_and_exit_code(self):
        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "import sys; sys.exit(2)"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.exit_code, 2)
        row = _read_status_row(constants.DB_PATH, result.commit_id)
        self.assertIsNotNone(row)
        status, _started, finished, exit_code = row
        self.assertEqual(status, "failed")
        self.assertEqual(exit_code, 2)
        self.assertIsNotNone(finished)

    def test_ailine_side_error_after_insert_marks_failed(self):
        """If an error is raised *after* the in_progress row is published but
        before the subprocess completes, the row must still be finalized as
        ``failed`` so the UI does not leave it stuck."""
        captured = {}

        def boom_after_insert(record_id, _mlflow_run_id):
            captured["run_id"] = record_id
            raise RuntimeError("ailine-side abort")

        with self.assertRaises(RuntimeError):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=self.config,
                on_run_started=boom_after_insert,
            )

        row = _read_status_row(constants.DB_PATH, captured["run_id"])
        self.assertIsNotNone(row)
        status, _started, finished, exit_code = row
        self.assertEqual(status, "failed")
        self.assertIsNotNone(finished)
        self.assertIsNone(exit_code)


if __name__ == "__main__":
    unittest.main()
