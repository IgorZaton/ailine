"""End-to-end tests for ``ailine purge``."""

import os
import subprocess
import sys
import tempfile
import unittest

from click.testing import CliRunner

from ailine.cli.manage import purge_command
from ailine.cli.track import track_command
from ailine.config import constants
from ailine.persistence.db import init_db
from ailine.snapshot.ignore import AILINEIGNORE_FILENAME, render_default_ailineignore


CFG_DEFAULT = (
    "project:\n"
    "  version: 1\n"
    "  mode: track\n"
    "track:\n"
    "  mlflow:\n"
    "    mode: none\n"
    "snapshot:\n"
    "  storage_dir: .ailine/snapshots\n"
)


def _bootstrap_repo(tmp: str, *, cfg_body: str = CFG_DEFAULT) -> str:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("hello\n")
    with open(os.path.join(repo, ".ailine.yml"), "w") as f:
        f.write(cfg_body)
    with open(os.path.join(repo, AILINEIGNORE_FILENAME), "w") as f:
        f.write(render_default_ailineignore())
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
    return repo


def _take_snapshot(repo: str, cfg_path: str) -> None:
    with open(os.path.join(repo, "feature.py"), "w") as f:
        f.write("def f(): return 1\n")
    runner = CliRunner()
    result = runner.invoke(
        track_command,
        ["--config", cfg_path, "--", sys.executable, "-c", "print('ok')"],
    )
    assert result.exit_code == 0, result.output


class PurgeCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.original_db = constants.DB_PATH
        self.addCleanup(self._restore_db)

        self.original_cwd = os.getcwd()
        self.addCleanup(lambda: os.chdir(self.original_cwd))

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def _bootstrap(self, *, cfg_body: str = CFG_DEFAULT) -> str:
        repo = _bootstrap_repo(self.tmp.name, cfg_body=cfg_body)
        # Mimic real-world layout: DB lives inside the repo's .ailine/, so
        # `ailine purge` wipes it together with the rest of the state dir.
        constants.DB_PATH = os.path.join(repo, ".ailine", "tree.db")
        init_db()
        os.chdir(repo)
        _take_snapshot(repo, os.path.join(repo, ".ailine.yml"))
        return repo

    def _purge(self, *, dry_run: bool = False, input_text: str = "y\n"):
        args = ["--config", os.path.join(os.getcwd(), ".ailine.yml")]
        if dry_run:
            args.append("--dry-run")
        runner = CliRunner()
        return runner.invoke(purge_command, args, input=input_text)

    def test_confirmed_purge_removes_state_config_and_ailineignore(self):
        repo = self._bootstrap()
        # Sanity: the targets exist after a snapshot.
        self.assertTrue(os.path.isdir(os.path.join(repo, ".ailine")))
        self.assertTrue(os.path.isfile(os.path.join(repo, ".ailine.yml")))
        self.assertTrue(
            os.path.isfile(os.path.join(repo, AILINEIGNORE_FILENAME))
        )

        result = self._purge(input_text="y\n")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertFalse(os.path.isdir(os.path.join(repo, ".ailine")))
        self.assertFalse(os.path.isfile(os.path.join(repo, ".ailine.yml")))
        self.assertFalse(
            os.path.isfile(os.path.join(repo, AILINEIGNORE_FILENAME))
        )

    def test_purge_leaves_mlruns_repo_and_user_files_alone(self):
        repo = self._bootstrap()
        os.makedirs(os.path.join(repo, "mlruns"), exist_ok=True)
        os.makedirs(os.path.join(repo, "data"), exist_ok=True)
        with open(os.path.join(repo, "data", "x.txt"), "w") as f:
            f.write("user-data\n")
        with open(os.path.join(repo, "mlruns", "marker"), "w") as f:
            f.write("mlflow\n")

        result = self._purge(input_text="y\n")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertTrue(os.path.isfile(os.path.join(repo, "mlruns", "marker")))
        self.assertTrue(os.path.isfile(os.path.join(repo, "data", "x.txt")))
        # README.md is also user-owned and stays.
        self.assertTrue(os.path.isfile(os.path.join(repo, "README.md")))

    def test_aborts_on_no(self):
        repo = self._bootstrap()
        result = self._purge(input_text="n\n")
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(os.path.isdir(os.path.join(repo, ".ailine")))
        self.assertTrue(os.path.isfile(os.path.join(repo, ".ailine.yml")))

    def test_aborts_on_empty_input(self):
        repo = self._bootstrap()
        result = self._purge(input_text="\n")
        self.assertNotEqual(result.exit_code, 0)
        self.assertTrue(os.path.isdir(os.path.join(repo, ".ailine")))
        self.assertTrue(os.path.isfile(os.path.join(repo, ".ailine.yml")))

    def test_dry_run_does_not_prompt_and_makes_no_changes(self):
        repo = self._bootstrap()
        ailine_before = sorted(os.listdir(os.path.join(repo, ".ailine")))
        # No input fed; if the command tried to prompt it would fail in CliRunner.
        result = self._purge(dry_run=True, input_text="")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("would remove", result.output)
        self.assertTrue(os.path.isdir(os.path.join(repo, ".ailine")))
        self.assertEqual(
            sorted(os.listdir(os.path.join(repo, ".ailine"))), ailine_before
        )
        self.assertTrue(os.path.isfile(os.path.join(repo, ".ailine.yml")))

    def test_non_default_storage_dir_outside_dotailine_is_removed(self):
        cfg = (
            "project:\n  version: 1\n  mode: track\n"
            "track:\n  mlflow:\n    mode: none\n"
            "snapshot:\n  storage_dir: out/snaps\n"
        )
        repo = self._bootstrap(cfg_body=cfg)
        external_storage = os.path.join(repo, "out", "snaps")
        self.assertTrue(os.path.isdir(external_storage))

        result = self._purge(input_text="y\n")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertFalse(os.path.isdir(external_storage))
        # `out/` itself was created by AIline; tolerate either outcome.

    def test_idempotent_when_nothing_left(self):
        repo = self._bootstrap()
        first = self._purge(input_text="y\n")
        self.assertEqual(first.exit_code, 0, msg=first.output)
        # Second purge: nothing-to-remove path. No prompt is raised; we still
        # feed input to be safe.
        second = self._purge(input_text="y\n")
        self.assertEqual(second.exit_code, 0, msg=second.output)
        self.assertIn("nothing to remove", second.output)
        self.assertFalse(os.path.isdir(os.path.join(repo, ".ailine")))


if __name__ == "__main__":
    unittest.main()
