"""End-to-end smoke tests for the ``ailine track`` Click command."""

import os
import subprocess
import sys
import tempfile
import unittest

from click.testing import CliRunner

from ailine.cli.track import track_command
from ailine.config import constants
from ailine.persistence.db import init_db


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
        storage = os.path.join(self.tmp.name, "snapshots")
        result = runner.invoke(
            track_command,
            ["--storage", storage, "--config", self.cfg_path,
             "--", sys.executable, "-c", "print('hello')"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_track_propagates_nonzero_exit(self):
        runner = CliRunner()
        storage = os.path.join(self.tmp.name, "snapshots")
        result = runner.invoke(
            track_command,
            ["--storage", storage, "--config", self.cfg_path,
             "--", sys.executable, "-c", "import sys; sys.exit(3)"],
        )
        self.assertEqual(result.exit_code, 3)

    def test_track_fails_when_no_config(self):
        runner = CliRunner()
        os.remove(self.cfg_path)
        storage = os.path.join(self.tmp.name, "snapshots")
        result = runner.invoke(
            track_command,
            ["--storage", storage, "--", sys.executable, "-c", "print(1)"],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("No .ailine.yml", result.output)

    def test_track_requires_argv(self):
        runner = CliRunner()
        result = runner.invoke(track_command, [])
        self.assertNotEqual(result.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
