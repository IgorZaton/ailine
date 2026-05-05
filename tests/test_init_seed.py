"""Tests for `ailine init-workspace` seeding `.ailine.yml` and `.ailineignore`."""

import os
import re
import tempfile
import unittest
from unittest.mock import patch

import yaml
from click.testing import CliRunner

from ailine.cli.init import WORKSPACE_TEMPLATE, init_workspace_command
from ailine.config import constants
from ailine.config.defaults import DEFAULT_TRACK_CONFIG


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
        self.assertIn("cleanup:", yaml_content)
        self.assertIn("with_mlflow: false", yaml_content)

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

    @patch("ailine.cli.init.init_state_dirs")
    def test_template_covers_all_default_track_mlflow_keys(self, _mock_state):
        """Parity guard: every DEFAULT_TRACK_CONFIG['mlflow'] key must appear
        in WORKSPACE_TEMPLATE so the generated `.ailine.yml` is deterministic
        and never relies on hidden defaults.
        """
        runner = CliRunner()
        result = runner.invoke(init_workspace_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)

        with open(constants.POLICY_PATH, "r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)

        emitted = parsed.get("track", {}).get("mlflow", {})
        for key in DEFAULT_TRACK_CONFIG["mlflow"].keys():
            self.assertIn(
                key,
                emitted,
                msg=f"track.mlflow.{key} missing from generated .ailine.yml",
            )

    @patch("ailine.cli.init.init_state_dirs")
    def test_prints_resolved_mlflow_environment_summary(self, _mock_state):
        runner = CliRunner()
        with patch.dict(os.environ, {}, clear=False):
            for key in (
                "AILINE_MLFLOW_URI",
                "AILINE_MLFLOW_UI_BASE",
                "MLFLOW_TRACKING_URI",
                "AILINE_STORAGE_DIR",
            ):
                os.environ.pop(key, None)
            result = runner.invoke(init_workspace_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("Resolved MLflow environment:", result.output)
        self.assertIn("tracking URI:", result.output)
        self.assertIn("UI base:", result.output)
        self.assertIn("storage dir:", result.output)
        self.assertIn("default(file://mlruns)", result.output)
        self.assertIn("export AILINE_MLFLOW_URI=", result.output)

    @patch("ailine.cli.init.init_state_dirs")
    def test_summary_reflects_mlflow_tracking_uri_when_set(self, _mock_state):
        runner = CliRunner()
        with patch.dict(
            os.environ,
            {"MLFLOW_TRACKING_URI": "http://10.0.0.5:5000"},
        ):
            os.environ.pop("AILINE_MLFLOW_URI", None)
            os.environ.pop("AILINE_MLFLOW_UI_BASE", None)
            result = runner.invoke(init_workspace_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("http://10.0.0.5:5000", result.output)
        self.assertIn("source: MLFLOW_TRACKING_URI", result.output)
        self.assertIn("derived(MLFLOW_TRACKING_URI)", result.output)

    def test_template_string_carries_link_strategy_default(self):
        """Sanity guard on the literal template body."""
        self.assertIn("link_strategy: tag", WORKSPACE_TEMPLATE)
        self.assertIn("link_poll_seconds:", WORKSPACE_TEMPLATE)
        self.assertIsNone(re.search(r"(?m)^\s*prelink:", WORKSPACE_TEMPLATE))


if __name__ == "__main__":
    unittest.main()
