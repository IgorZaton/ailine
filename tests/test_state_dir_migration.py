"""Tests for ``migrate_legacy_state_artifacts`` (legacy artifact relocation).

Older AIline versions wrote ``ailine_tree.db``, ``ailine.log`` and
``ailine_config.txt`` next to ``.ailine.yml`` in the project root. The
migration helper relocates them under ``.ailine/`` on first use of any
``ailine`` command without aborting if a single move fails.
"""

import logging
import os
import tempfile
import unittest

from ailine.run.migration import migrate_legacy_state_artifacts


class StateDirMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cwd_before = os.getcwd()
        os.chdir(self.tmp.name)
        self.addCleanup(os.chdir, self.cwd_before)
        self.state_dir = os.path.join(self.tmp.name, ".ailine")
        self.mapping = (
            ("ailine_tree.db", os.path.join(self.state_dir, "tree.db")),
            ("ailine.log", os.path.join(self.state_dir, "ailine.log")),
            ("ailine_config.txt", os.path.join(self.state_dir, "demo-config.txt")),
        )

    def _seed_legacy(self, name: str, payload: bytes = b"x") -> str:
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(payload)
        return path

    def test_legacy_db_moves_to_state_dir(self):
        legacy = self._seed_legacy("ailine_tree.db", b"sqlite-bytes")
        migrate_legacy_state_artifacts(self.mapping)
        target = self.mapping[0][1]
        self.assertFalse(os.path.exists(legacy))
        self.assertTrue(os.path.exists(target))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"sqlite-bytes")

    def test_legacy_log_and_config_migrate_equivalently(self):
        self._seed_legacy("ailine.log", b"log\n")
        self._seed_legacy("ailine_config.txt", b"git@example.com:x.git")
        migrate_legacy_state_artifacts(self.mapping)
        for legacy_name, new_path in self.mapping[1:]:
            self.assertFalse(
                os.path.exists(os.path.join(self.tmp.name, legacy_name)),
                msg=f"{legacy_name} should have been removed after migration",
            )
            self.assertTrue(os.path.exists(new_path), msg=new_path)

    def test_when_both_legacy_and_new_exist_legacy_is_left_in_place(self):
        legacy = self._seed_legacy("ailine_tree.db", b"legacy")
        target = self.mapping[0][1]
        os.makedirs(self.state_dir, exist_ok=True)
        with open(target, "wb") as f:
            f.write(b"new")

        with self.assertLogs(level=logging.WARNING) as captured:
            migrate_legacy_state_artifacts(self.mapping)

        self.assertTrue(os.path.exists(legacy))
        self.assertTrue(os.path.exists(target))
        with open(target, "rb") as f:
            self.assertEqual(f.read(), b"new")
        joined = "\n".join(captured.output)
        self.assertIn(self.mapping[0][0], joined)
        self.assertIn(target, joined)
        self.assertTrue(os.path.exists(legacy))

    def test_idempotent_when_no_legacy_files(self):
        # Should be a no-op and not create the state dir on its own.
        migrate_legacy_state_artifacts(self.mapping)
        self.assertFalse(os.path.exists(self.state_dir))


if __name__ == "__main__":
    unittest.main()
