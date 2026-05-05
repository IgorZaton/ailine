"""End-to-end smoke tests for the ``ailine track`` Click command."""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ailine.cli.track import track_command
from ailine.config import constants
from ailine.persistence.db import init_db


def _bootstrap_repo(
    tmp: str,
    *,
    mlflow_mode: str | None = None,
    repo_name: str = "repo",
    storage_dir: str | None = ".ailine/snapshots",
) -> str:
    repo = os.path.join(tmp, repo_name)
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("hi\n")
    cfg_lines = ["project:\n", "  version: 1\n", "  mode: track\n"]
    if mlflow_mode is not None:
        cfg_lines.extend(["track:\n", "  mlflow:\n", f"    mode: {mlflow_mode}\n"])
    if storage_dir is not None:
        cfg_lines.extend(["snapshot:\n", f"  storage_dir: {storage_dir}\n"])
    with open(os.path.join(repo, ".ailine.yml"), "w") as f:
        f.writelines(cfg_lines)
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
    return repo


class TrackCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _bootstrap_repo(self.tmp.name)
        self.cfg_path = os.path.join(self.repo, ".ailine.yml")

        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)

        self.original_cwd = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(lambda: os.chdir(self.original_cwd))

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def test_track_runs_and_propagates_zero_exit(self):
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            ["--config", self.cfg_path, "--", sys.executable, "-c", "print('hello')"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_track_propagates_nonzero_exit(self):
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            [
                "--config",
                self.cfg_path,
                "--",
                sys.executable,
                "-c",
                "import sys; sys.exit(3)",
            ],
        )
        self.assertEqual(result.exit_code, 3)

    def test_track_fails_when_no_config(self):
        runner = CliRunner()
        os.remove(self.cfg_path)
        result = runner.invoke(
            track_command,
            ["--", sys.executable, "-c", "print(1)"],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No .ailine.yml", result.output)

    def test_track_requires_argv(self):
        runner = CliRunner()
        result = runner.invoke(track_command, [])
        self.assertNotEqual(result.exit_code, 0)

    def test_track_storage_flag_no_longer_track_option(self):
        # ``--storage`` is no longer a track option; with ignore_unknown_options
        # it falls through to argv. Either the subprocess fails because
        # ``--storage`` is not a real program, or click emits a non-zero exit.
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            [
                "--config",
                self.cfg_path,
                "--",
                "--storage",
                "/tmp/whatever",
                sys.executable,
                "-c",
                "print(0)",
            ],
        )
        self.assertNotEqual(result.exit_code, 0)

    def _make_repo_dirty(self):
        with open(os.path.join(self.repo, "uncommitted.txt"), "w") as f:
            f.write("dirty\n")

    def test_track_storage_dir_from_yaml(self):
        self._make_repo_dirty()
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            ["--config", self.cfg_path, "--", sys.executable, "-c", "print(0)"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        expected = os.path.join(self.repo, ".ailine", "snapshots")
        self.assertTrue(
            os.path.isdir(expected),
            msg=f"expected snapshots dir {expected} to exist",
        )

    def test_track_env_override_beats_yaml_storage_dir(self):
        self._make_repo_dirty()
        runner = CliRunner()
        env_storage = os.path.join(self.tmp.name, "env-snapshots")
        result = runner.invoke(
            track_command,
            ["--config", self.cfg_path, "--", sys.executable, "-c", "print(0)"],
            env={"AILINE_STORAGE_DIR": env_storage},
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertTrue(
            os.path.isdir(env_storage),
            msg=f"AILINE_STORAGE_DIR={env_storage} must win over yaml",
        )

    def test_track_custom_name_persisted(self):
        runner = CliRunner()
        label = "my-baseline-v2"
        result = runner.invoke(
            track_command,
            [
                "--config",
                self.cfg_path,
                "--name",
                label,
                "--",
                sys.executable,
                "-c",
                "print('n')",
            ],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            row = conn.execute("SELECT record_name FROM tree LIMIT 1").fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], label)

    def test_track_preview_stderr_shows_name(self):
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            ["--config", self.cfg_path, "--", sys.executable, "-c", "print(0)"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        out = result.output
        self.assertIn("ailine track:", out)
        self.assertIn("name=", out)
        self.assertIn("repo=", out)

    def test_track_preview_includes_mlflow_run_name_when_wrap(self):
        os.chdir(self.original_cwd)
        wrap_repo = _bootstrap_repo(self.tmp.name, mlflow_mode="wrap", repo_name="repo_wrap")
        os.chdir(wrap_repo)
        try:
            runner = CliRunner()
            cfg = os.path.join(wrap_repo, ".ailine.yml")
            with patch("ailine.run.session.mlflow.start_run") as start_run, patch(
                "ailine.run.session.mlflow.active_run"
            ) as active_run:
                ctx = start_run.return_value
                ctx.__enter__.return_value = None
                ctx.__exit__.return_value = False
                active_run.return_value.info.run_id = "rid-wrap"
                result = runner.invoke(
                    track_command,
                    [
                        "--config",
                        cfg,
                        "--name",
                        "wrap-preview-label",
                        "--",
                        sys.executable,
                        "-c",
                        "print(0)",
                    ],
                )
            self.assertEqual(result.exit_code, 0, msg=result.output)
            self.assertIn("mlflow_run_name='wrap-preview-label'", result.output)
        finally:
            os.chdir(self.repo)


if __name__ == "__main__":
    unittest.main()
