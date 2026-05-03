"""Tests for the single config validation surface."""

import os
import tempfile
import unittest

from ailine.config.validate import (
    ConfigValidationError,
    validate_config,
)


class ValidateConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg_path = os.path.join(self.tmp.name, ".ailine.yml")

    def _write(self, body: str) -> str:
        with open(self.cfg_path, "w", encoding="utf-8") as f:
            f.write(body)
        return self.cfg_path

    def test_missing_file_returns_defaults_with_flag(self):
        result = validate_config(os.path.join(self.tmp.name, "nope.yml"))
        self.assertFalse(result.config_exists)
        self.assertEqual(result.project["version"], 1)
        self.assertEqual(result.track["mlflow"]["mode"], "inherit")
        self.assertEqual(result.track["mlflow"]["inherit_name_sync"], "auto")
        self.assertEqual(result.snapshot["large_file_mode"], "prompt")

    def test_minimal_config_is_valid(self):
        path = self._write(
            "project:\n  version: 1\n  mode: track\n"
            "track:\n  mlflow:\n    mode: inherit\n"
        )
        result = validate_config(path)
        self.assertTrue(result.config_exists)
        self.assertEqual(result.project["mode"], "track")
        self.assertEqual(result.track["mlflow"]["mode"], "inherit")
        self.assertEqual(result.track["dvc"]["verify"], "off")  # default

    def test_unknown_top_level_key_warns_but_passes(self):
        path = self._write("project:\n  version: 1\nunknown_block: 1\n")
        result = validate_config(path)
        self.assertTrue(result.config_exists)
        self.assertTrue(any("unknown_block" in w for w in result.warnings))

    def test_invalid_mlflow_mode_raises(self):
        path = self._write("track:\n  mlflow:\n    mode: bogus\n")
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(path)
        self.assertIn("track.mlflow.mode", str(ctx.exception))

    def test_invalid_mlflow_inherit_name_sync_raises(self):
        path = self._write("track:\n  mlflow:\n    inherit_name_sync: maybe\n")
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(path)
        self.assertIn("track.mlflow.inherit_name_sync", str(ctx.exception))

    def test_invalid_dvc_verify_level_raises(self):
        path = self._write('track:\n  dvc:\n    verify: "loud"\n')
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(path)
        self.assertIn("track.dvc.verify", str(ctx.exception))

    def test_verify_commands_must_be_list_of_argv_lists(self):
        path = self._write(
            "track:\n  dvc:\n    verify_commands:\n      - 'dvc status'\n"
        )
        with self.assertRaises(ConfigValidationError):
            validate_config(path)

    def test_unsupported_project_version_raises(self):
        path = self._write("project:\n  version: 99\n")
        with self.assertRaises(ConfigValidationError) as ctx:
            validate_config(path)
        self.assertIn("project.version", str(ctx.exception))

    def test_removed_dvc_keys_are_rejected(self):
        for removed_key in ("mode", "scope", "auto_pull_missing", "status_verbose_limit"):
            with self.subTest(key=removed_key):
                path = self._write(f"dvc:\n  {removed_key}: x\n")
                with self.assertRaises(ConfigValidationError) as ctx:
                    validate_config(path)
                self.assertIn(f"dvc.{removed_key}", str(ctx.exception))

    def test_top_level_must_be_mapping(self):
        path = self._write("- not\n- a\n- mapping\n")
        with self.assertRaises(ConfigValidationError):
            validate_config(path)

    def test_environment_packages_must_be_strings(self):
        path = self._write("environment:\n  packages:\n    - 1\n")
        with self.assertRaises(ConfigValidationError):
            validate_config(path)


if __name__ == "__main__":
    unittest.main()
