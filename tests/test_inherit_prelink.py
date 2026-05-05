"""Tests for the inherit-mode pre-link path.

In ``track.mlflow.mode: inherit`` with ``link_strategy: prelink``, AIline
pre-creates an MLflow run via the MLflow client API and exports
``MLFLOW_RUN_ID`` to the child process so a plain ``mlflow.start_run()``
in the user's script resumes that run. The lineage row's ``mlflow_run``
column is populated *before* the subprocess starts and the post-hoc
search-runs lookup is skipped. The default linking strategy is ``tag``;
these tests force ``link_strategy: prelink`` to exercise the legacy path.
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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


def _read_mlflow_run(db_path: str, run_id: str):
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute("SELECT mlflow_run FROM tree WHERE id = ?", (run_id,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _make_client_mock(run_id: str = "prelinked-run-1") -> MagicMock:
    client = MagicMock()
    client.create_run.return_value.info.run_id = run_id
    client.get_experiment_by_name.return_value = None
    return client


class InheritPrelinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _bootstrap_repo(self.tmp.name)
        self.storage = os.path.join(self.tmp.name, "snapshots")

        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)

        # Strip env vars that would otherwise leak between tests and into the
        # session under test.
        self._saved_env = {
            key: os.environ.pop(key)
            for key in ("MLFLOW_RUN_ID", "MLFLOW_EXPERIMENT_ID", "MLFLOW_EXPERIMENT_NAME")
            if key in os.environ
        }
        self.addCleanup(self._restore_env)

        self.cfg_path = os.path.join(self.repo, ".ailine.yml")

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def _restore_env(self):
        for key in ("MLFLOW_RUN_ID", "MLFLOW_EXPERIMENT_ID", "MLFLOW_EXPERIMENT_NAME"):
            os.environ.pop(key, None)
        for key, value in self._saved_env.items():
            os.environ[key] = value

    def _capture_subprocess_env(self):
        """Patch subprocess.run so we can inspect the env passed to the child."""
        observed = {}
        real_run = subprocess.run

        def fake_run(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and cmd[0] == sys.executable:
                observed["env"] = kwargs.get("env")
            return real_run(*args, **kwargs)

        return observed, fake_run

    def test_prelink_default_inherit_populates_mlflow_run_in_db(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        client = _make_client_mock("prelinked-run-1")
        with patch("ailine.run.session.MlflowClient", return_value=client), patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child"
        ) as post_hoc:
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print('ok')"],
                storage=self.storage,
                config=cfg,
            )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.mlflow_run_id, "prelinked-run-1")
        self.assertEqual(
            _read_mlflow_run(constants.DB_PATH, result.commit_id), "prelinked-run-1"
        )
        client.create_run.assert_called_once()
        # Pre-creation made post-hoc search redundant.
        post_hoc.assert_not_called()

    def test_prelink_injects_mlflow_run_id_into_child_env(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        client = _make_client_mock("env-prelinked-run")
        observed, fake_run = self._capture_subprocess_env()
        with patch("ailine.run.session.MlflowClient", return_value=client), patch(
            "ailine.run.session.subprocess.run", side_effect=fake_run
        ):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        env = observed.get("env")
        self.assertIsNotNone(env)
        self.assertEqual(env.get("MLFLOW_RUN_ID"), "env-prelinked-run")

    def test_prelink_does_not_overwrite_user_supplied_run_id(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        os.environ["MLFLOW_RUN_ID"] = "user-run-id"
        client = _make_client_mock("ailine-prelinked")
        observed, fake_run = self._capture_subprocess_env()
        with patch("ailine.run.session.MlflowClient", return_value=client), patch(
            "ailine.run.session.subprocess.run", side_effect=fake_run
        ):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        env = observed.get("env")
        self.assertIsNotNone(env)
        self.assertEqual(env.get("MLFLOW_RUN_ID"), "user-run-id")

    def test_prelink_disabled_falls_back_to_post_hoc_lookup(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "none"
        observed, fake_run = self._capture_subprocess_env()
        client = _make_client_mock("should-not-be-used")
        with patch("ailine.run.session.MlflowClient", return_value=client), patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value="post-hoc-run",
        ) as post_hoc, patch(
            "ailine.run.session.subprocess.run", side_effect=fake_run
        ):
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        # MlflowClient may be invoked by inherit-name-sync, but pre-creation
        # must be skipped entirely when prelink is off.
        client.create_run.assert_not_called()
        post_hoc.assert_called_once()
        self.assertEqual(result.mlflow_run_id, "post-hoc-run")
        env = observed.get("env")
        if env is not None:
            self.assertNotIn("MLFLOW_RUN_ID", env)

    def test_prelink_create_run_failure_falls_back_silently(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        client = MagicMock()
        client.create_run.side_effect = RuntimeError("backend unreachable")
        client.get_experiment_by_name.return_value = None
        observed, fake_run = self._capture_subprocess_env()
        with patch("ailine.run.session.MlflowClient", return_value=client), patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value=None,
        ) as post_hoc, patch(
            "ailine.run.session.subprocess.run", side_effect=fake_run
        ):
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        self.assertEqual(result.exit_code, 0)
        self.assertIsNone(result.mlflow_run_id)
        # Post-hoc lookup is the fallback when prelink fails.
        post_hoc.assert_called_once()
        env = observed.get("env")
        if env is not None:
            self.assertNotIn("MLFLOW_RUN_ID", env)

    def test_wrap_mode_does_not_invoke_prelink(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "wrap"
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        with patch("ailine.run.session.MlflowClient") as client_cls, patch(
            "ailine.run.session.mlflow.start_run"
        ) as start_run:
            ctx = start_run.return_value
            ctx.__enter__.return_value = None
            ctx.__exit__.return_value = False
            with patch("ailine.run.session.mlflow.active_run") as active_run:
                active_run.return_value.info.run_id = "wrap-run-1"
                result = run_tracked_command(
                    git_root=self.repo,
                    argv=[sys.executable, "-c", "print(1)"],
                    storage=self.storage,
                    config=cfg,
                )
        client_cls.assert_not_called()
        self.assertEqual(result.mlflow_run_id, "wrap-run-1")

    def test_prelink_uses_experiment_id_from_env_when_set(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        os.environ["MLFLOW_EXPERIMENT_ID"] = "42"
        client = _make_client_mock("exp-run")
        with patch("ailine.run.session.MlflowClient", return_value=client):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        kwargs = client.create_run.call_args.kwargs
        self.assertEqual(kwargs.get("experiment_id"), "42")

    def test_prelink_creates_experiment_by_name_when_missing(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["link_strategy"] = "prelink"
        os.environ["MLFLOW_EXPERIMENT_NAME"] = "demo-experiment"
        client = MagicMock()
        client.create_run.return_value.info.run_id = "named-exp-run"
        client.get_experiment_by_name.return_value = None
        client.create_experiment.return_value = "99"
        with patch("ailine.run.session.MlflowClient", return_value=client):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        client.create_experiment.assert_called_once_with("demo-experiment")
        kwargs = client.create_run.call_args.kwargs
        self.assertEqual(kwargs.get("experiment_id"), "99")


if __name__ == "__main__":
    unittest.main()
