"""Tests for `ailine init-workspace` seeding `.ailine.yml` and `.ailineignore`."""

import os
import tempfile
import unittest
from unittest.mock import patch

from click.testing import CliRunner

from ailine.cli.init import init_workspace_command
from ailine.config import constants


class InitWorkspaceSeedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.original_cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(lambda: os.chdir(self.original_cwd))

        self.original_policy_path = constants.POLICY_PATH
        constants.POLICY_PATH = os.path.join(self.tmp.name, ".ailine.yml")
        self.addCleanup(self._restore_policy_path)

        self.original_state_dir = constants.STATE_DIR
        constants.STATE_DIR = os.path.join(self.tmp.name, ".ailine")
        self.addCleanup(self._restore_state_dir)

    def _restore_policy_path(self):
        constants.POLICY_PATH = self.original_policy_path

    def _restore_state_dir(self):
        constants.STATE_DIR = self.original_state_dir

    @patch("ailine.cli.init.init_state_dirs")
    def test_creates_yaml_and_ailineignore(self, _mock_state):
        runner = CliRunner()
        result = runner.invoke(init_workspace_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)

        ignore_path = os.path.join(self.tmp.name, ".ailineignore")
        self.assertTrue(os.path.exists(ignore_path))
        with open(ignore_path, "r", encoding="utf-8") as f:
            ignore_content = f.read()
        # Contains representative defaults the user explicitly cares about.
        for needle in (".cursor/", ".claude/", "mlruns/", "__pycache__/"):
            self.assertIn(needle, ignore_content, msg=needle)

        with open(constants.POLICY_PATH, "r", encoding="utf-8") as f:
            yaml_content = f.read()
        # Migration: yaml template no longer carries `exclude_globs`.
        self.assertNotIn("exclude_globs", yaml_content)

    @patch("ailine.cli.init.init_state_dirs")
    def test_preserves_user_ailineignore_without_force(self, _mock_state):
        ignore_path = os.path.join(self.tmp.name, ".ailineignore")
        with open(ignore_path, "w", encoding="utf-8") as f:
            f.write("custom_only.txt\n")

        runner = CliRunner()
        result = runner.invoke(init_workspace_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        with open(ignore_path, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "custom_only.txt\n")
        self.assertIn("already exists", result.output)

    @patch("ailine.cli.init.init_state_dirs")
    def test_force_overwrites_user_ailineignore(self, _mock_state):
        ignore_path = os.path.join(self.tmp.name, ".ailineignore")
        with open(ignore_path, "w", encoding="utf-8") as f:
            f.write("custom_only.txt\n")

        runner = CliRunner()
        result = runner.invoke(init_workspace_command, ["--force"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        with open(ignore_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertNotEqual(content, "custom_only.txt\n")
        self.assertIn(".cursor/", content)


if __name__ == "__main__":
    unittest.main()
