"""Tests for snapshot storage directory resolution."""

import os
import tempfile
import unittest
from unittest.mock import patch

from ailine.config import constants
from ailine.snapshot.storage import resolve_storage_dir


class StorageResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name

    def test_default_resolves_under_state_dir(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AILINE_STORAGE_DIR", None)
            resolved = resolve_storage_dir({"storage_dir": None}, self.repo)
        self.assertEqual(
            os.path.abspath(resolved),
            os.path.abspath(constants.DEFAULT_STORAGE_DIR),
        )

    def test_yaml_relative_resolved_against_repo_root(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AILINE_STORAGE_DIR", None)
            resolved = resolve_storage_dir({"storage_dir": ".ailine/snapshots"}, self.repo)
        self.assertEqual(resolved, os.path.join(self.repo, ".ailine/snapshots"))

    def test_yaml_absolute_path_kept_as_is(self):
        absolute = os.path.join(self.tmp.name, "abs-snaps")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AILINE_STORAGE_DIR", None)
            resolved = resolve_storage_dir({"storage_dir": absolute}, self.repo)
        self.assertEqual(resolved, absolute)

    def test_env_override_wins_over_yaml(self):
        env_path = os.path.join(self.tmp.name, "env-snaps")
        with patch.dict(os.environ, {"AILINE_STORAGE_DIR": env_path}):
            resolved = resolve_storage_dir({"storage_dir": "yaml-snaps"}, self.repo)
        self.assertEqual(resolved, os.path.abspath(env_path))

    def test_none_snapshot_cfg_returns_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AILINE_STORAGE_DIR", None)
            resolved = resolve_storage_dir(None, self.repo)
        self.assertEqual(
            os.path.abspath(resolved),
            os.path.abspath(constants.DEFAULT_STORAGE_DIR),
        )


if __name__ == "__main__":
    unittest.main()
