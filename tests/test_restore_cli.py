"""End-to-end tests for ``ailine restore``."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from click.testing import CliRunner

from ailine.cli.restore import restore_command
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
        f.write("hello\n")
    with open(os.path.join(repo, ".ailine.yml"), "w") as f:
        f.write(
            "project:\n"
            "  version: 1\n"
            "  mode: track\n"
            "track:\n"
            "  mlflow:\n"
            "    mode: none\n"
            "snapshot:\n"
            "  storage_dir: .ailine/snapshots\n"
        )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "init"], check=True)
    return repo


def _latest_snapshot_id(db_path: str) -> str:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM tree WHERE type = 'snapshot' ORDER BY timestamp DESC LIMIT 1"
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        raise AssertionError("no snapshot row in DB")
    return row[0]


class RestoreCommandTests(unittest.TestCase):
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

    def _take_snapshot(self) -> str:
        with open(os.path.join(self.repo, "feature.py"), "w") as f:
            f.write("def feature():\n    return 'snapshot-content'\n")
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            ["--config", self.cfg_path, "--", sys.executable, "-c", "print('ok')"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        return _latest_snapshot_id(constants.DB_PATH)

    def _commit_clean(self) -> None:
        subprocess.run(["git", "-C", self.repo, "add", "-A"], check=True)
        subprocess.run(["git", "-C", self.repo, "commit", "-q", "-m", "snap state"], check=True)

    def test_unknown_snapshot_id_fails_fast(self):
        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, "deadbeef" * 8],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Snapshot not found", result.output)

    def test_dry_run_makes_no_changes(self):
        snap_id = self._take_snapshot()
        self._commit_clean()
        os.remove(os.path.join(self.repo, "feature.py"))
        with open(os.path.join(self.repo, "extra.py"), "w") as f:
            f.write("intruder\n")
        self._commit_clean()

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, "--dry-run", snap_id],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertFalse(os.path.exists(os.path.join(self.repo, "feature.py")))
        self.assertTrue(os.path.exists(os.path.join(self.repo, "extra.py")))
        self.assertIn("would-write", result.output)
        self.assertIn("would-delete", result.output)

    def test_strict_sync_writes_and_deletes(self):
        snap_id = self._take_snapshot()
        self._commit_clean()
        # Drift the worktree away from the snapshot state.
        os.remove(os.path.join(self.repo, "feature.py"))
        with open(os.path.join(self.repo, "extra.py"), "w") as f:
            f.write("intruder\n")
        self._commit_clean()

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, snap_id],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        feature_path = os.path.join(self.repo, "feature.py")
        self.assertTrue(os.path.exists(feature_path))
        with open(feature_path, "r", encoding="utf-8") as f:
            self.assertIn("snapshot-content", f.read())
        self.assertFalse(os.path.exists(os.path.join(self.repo, "extra.py")))
        self.assertTrue(os.path.exists(os.path.join(self.repo, ".git")))

    def test_dirty_tree_blocks_without_force(self):
        snap_id = self._take_snapshot()
        self._commit_clean()
        # Make the worktree dirty (uncommitted change).
        with open(os.path.join(self.repo, "README.md"), "a") as f:
            f.write("uncommitted\n")

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, snap_id],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Working tree is dirty", result.output)

    def test_dirty_tree_allowed_with_force(self):
        snap_id = self._take_snapshot()
        self._commit_clean()
        with open(os.path.join(self.repo, "README.md"), "a") as f:
            f.write("uncommitted\n")

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, "--force", snap_id],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)

    def test_dirty_ignored_paths_do_not_block_without_force(self):
        snap_id = self._take_snapshot()
        self._commit_clean()
        # `.cursor/` is an AIline default-ignore; a dirty file there must not
        # block restore (snapshot scan would not have captured it either).
        os.makedirs(os.path.join(self.repo, ".cursor", "rules"), exist_ok=True)
        with open(os.path.join(self.repo, ".cursor", "rules", "tmp.mdc"), "w") as f:
            f.write("local-only\n")

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, "--dry-run", snap_id],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertTrue(
            os.path.exists(os.path.join(self.repo, ".cursor", "rules", "tmp.mdc"))
        )

    def test_missing_object_aborts_before_mutation(self):
        snap_id = self._take_snapshot()
        self._commit_clean()
        # Remove the entire object store: every needed object is now missing.
        objects_root = os.path.join(self.repo, ".ailine", "snapshots", "objects")
        self.assertTrue(os.path.isdir(objects_root))
        import shutil

        shutil.rmtree(objects_root)

        before = sorted(os.listdir(self.repo))
        runner = CliRunner()
        # --force is needed because removing the object store dirties the worktree;
        # the test asserts that the missing-object preflight still runs after that gate.
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, "--force", snap_id],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("missing from", result.output)
        # Worktree must be untouched.
        self.assertEqual(sorted(os.listdir(self.repo)), before)

    def test_legacy_metadata_rejected(self):
        snap_id = self._take_snapshot()
        # Mutate the metadata to a legacy/unknown format.
        meta_path = os.path.join(
            self.repo, ".ailine", "snapshots", f"{snap_id}.metadata.json"
        )
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        meta["format"] = "legacy-tarball"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, snap_id],
        )
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("not in 'objects-v1'", result.output)

    def test_protects_dot_git_and_dot_ailine(self):
        snap_id = self._take_snapshot()
        self._commit_clean()

        runner = CliRunner()
        result = runner.invoke(
            restore_command,
            ["--config", self.cfg_path, snap_id],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        # .git stays. .ailine stays (it holds the storage dir we just read from).
        self.assertTrue(os.path.isdir(os.path.join(self.repo, ".git")))
        self.assertTrue(os.path.isdir(os.path.join(self.repo, ".ailine")))


if __name__ == "__main__":
    unittest.main()
