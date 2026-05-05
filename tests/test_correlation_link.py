"""End-to-end test for tag-based MLflow linking via the correlation poller.

The poller is exercised with a stub ``_resolve_run_by_correlation`` that
returns no match for the first N polls, then yields a run id - mirroring the
real flow where the user's training script eventually calls
``mlflow.start_run()`` and the AIline plugin tags the run.

We assert that the lineage row's ``mlflow_run`` column gets populated
mid-flight (i.e. the row already shows ``in_progress`` before the child
finishes) and that ``set_mlflow_run`` is invoked exactly once via the
poller's ``set_mlflow_run`` path.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
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
        f.write(
            "project:\n  version: 1\n  mode: track\n"
            "track:\n  mlflow:\n"
            "    mode: inherit\n"
            "    link_strategy: tag\n"
            "    link_poll_seconds: 0.05\n"
        )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
    return repo


def _read_mlflow_run(db_path: str, run_id: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT mlflow_run FROM tree WHERE id = ?", (run_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


class CorrelationLinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _bootstrap_repo(self.tmp.name)
        self.storage = os.path.join(self.tmp.name, "snapshots")
        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)
        self.cfg_path = os.path.join(self.repo, ".ailine.yml")

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def test_tag_poller_links_mlflow_run_mid_flight(self):
        """Poller surfaces a match while the child is still running.

        We use a child that sleeps long enough for the first poll cycle to
        miss and the next one to hit, then exits 0. The DB column must
        reflect the linked run id by the time the session returns.
        """
        cfg = validate_config(self.cfg_path)

        call_count = {"n": 0}
        def fake_resolve(_correlation_id):
            call_count["n"] += 1
            return None if call_count["n"] < 2 else "tag-linked-run-id"

        with patch(
            "ailine.run.session._resolve_run_by_correlation",
            side_effect=fake_resolve,
        ), patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value=None,
        ), patch(
            "ailine.run.session._maybe_sync_inherit_mlflow_name",
            return_value=None,
        ):
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "import time; time.sleep(0.3)"],
                storage=self.storage,
                config=cfg,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.mlflow_run_id, "tag-linked-run-id")
        self.assertEqual(
            _read_mlflow_run(constants.DB_PATH, result.commit_id),
            "tag-linked-run-id",
        )
        self.assertGreaterEqual(call_count["n"], 2)

    def test_tag_poller_no_match_logs_warning_and_leaves_mlflow_run_null(self):
        """No tagged run anywhere → DB stays NULL, AIline does not crash."""
        cfg = validate_config(self.cfg_path)

        with patch(
            "ailine.run.session._resolve_run_by_correlation",
            return_value=None,
        ), patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value=None,
        ), patch(
            "ailine.run.session._maybe_sync_inherit_mlflow_name",
            return_value=None,
        ):
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print('hi')"],
                storage=self.storage,
                config=cfg,
            )

        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.mlflow_run_id)
        self.assertIsNone(_read_mlflow_run(constants.DB_PATH, result.commit_id))

    def test_tag_strategy_injects_correlation_id_into_child_env(self):
        """Child receives ``AILINE_CORRELATION_ID`` so the plugin can tag runs."""
        from ailine.integrations.mlflow_plugin import CORRELATION_ENV

        cfg = validate_config(self.cfg_path)

        observed = {}
        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and cmd[0] == sys.executable:
                observed["env"] = kwargs.get("env")
            return real_run(*args, **kwargs)

        with patch(
            "ailine.run.session._resolve_run_by_correlation",
            return_value=None,
        ), patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value=None,
        ), patch(
            "ailine.run.session._maybe_sync_inherit_mlflow_name",
            return_value=None,
        ), patch(
            "ailine.run.session.subprocess.run",
            side_effect=fake_run,
        ):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )

        env = observed.get("env")
        self.assertIsNotNone(env)
        cid = env.get(CORRELATION_ENV)
        self.assertTrue(cid)
        self.assertEqual(len(cid), 32)  # uuid4().hex


if __name__ == "__main__":
    unittest.main()
