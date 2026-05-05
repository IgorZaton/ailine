"""End-to-end tests for ``ailine remove``."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from ailine.cli.manage import remove_command
from ailine.cli.track import track_command
from ailine.config import constants
from ailine.persistence.db import init_db


def _bootstrap_repo(tmp: str, *, with_mlflow_default: bool | None = None) -> str:
    repo = os.path.join(tmp, "repo")
    os.makedirs(repo)
    subprocess.run(["git", "init", "-q", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "t@t.t"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "t"], check=True)
    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("hello\n")
    cfg_lines = [
        "project:",
        "  version: 1",
        "  mode: track",
        "track:",
        "  mlflow:",
        "    mode: none",
        "snapshot:",
        "  storage_dir: .ailine/snapshots",
    ]
    if with_mlflow_default is not None:
        cfg_lines += [
            "cleanup:",
            "  remove:",
            f"    with_mlflow: {'true' if with_mlflow_default else 'false'}",
        ]
    with open(os.path.join(repo, ".ailine.yml"), "w") as f:
        f.write("\n".join(cfg_lines) + "\n")
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


def _set_mlflow_run(db_path: str, row_id: str, run_id: str | None) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE tree SET mlflow_run = ? WHERE id = ?", (run_id, row_id)
        )
        conn.commit()
    finally:
        conn.close()


class RemoveCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)

        self.original_cwd = os.getcwd()
        self.addCleanup(lambda: os.chdir(self.original_cwd))

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def _bootstrap(self, with_mlflow_default: bool | None = None) -> tuple[str, str]:
        repo = _bootstrap_repo(self.tmp.name, with_mlflow_default=with_mlflow_default)
        cfg_path = os.path.join(repo, ".ailine.yml")
        os.chdir(repo)
        return repo, cfg_path

    def _take_snapshot(self, repo: str, cfg_path: str, *, content: str) -> str:
        path = os.path.join(repo, "feature.py")
        with open(path, "w") as f:
            f.write(content)
        runner = CliRunner()
        result = runner.invoke(
            track_command,
            ["--config", cfg_path, "--", sys.executable, "-c", "print('ok')"],
        )
        self.assertEqual(result.exit_code, 0, msg=result.output)
        snap_id = _latest_snapshot_id(constants.DB_PATH)
        # Commit the new state so the next snapshot has a clean baseline.
        subprocess.run(["git", "-C", repo, "add", "-A"], check=True)
        subprocess.run(
            ["git", "-C", repo, "commit", "-q", "-m", f"add {content[:8]}"],
            check=True,
        )
        return snap_id

    def _run_remove(
        self,
        cfg_path: str,
        record_id: str,
        *,
        with_mlflow: str | None = None,
        dry_run: bool = False,
    ):
        args = ["--config", cfg_path]
        if with_mlflow is not None:
            args += ["--with-mlflow", with_mlflow]
        if dry_run:
            args.append("--dry-run")
        args.append(record_id)
        runner = CliRunner()
        return runner.invoke(remove_command, args)

    def _row_exists(self, record_id: str) -> bool:
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            cur = conn.execute("SELECT 1 FROM tree WHERE id = ?", (record_id,))
            return cur.fetchone() is not None
        finally:
            conn.close()

    def test_unknown_record_id_fails_fast(self):
        _, cfg_path = self._bootstrap()
        result = self._run_remove(cfg_path, "deadbeef" * 8)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("Record not found", result.output)

    def test_remove_deletes_artifacts_and_db_row(self):
        repo, cfg_path = self._bootstrap()
        snap_id = self._take_snapshot(repo, cfg_path, content="def f(): return 1\n")

        manifest = os.path.join(repo, ".ailine", "snapshots", f"{snap_id}.manifest.json")
        metadata = os.path.join(repo, ".ailine", "snapshots", f"{snap_id}.metadata.json")
        diff = os.path.join(repo, ".ailine", "snapshots", f"{snap_id}.diff.patch")
        self.assertTrue(os.path.exists(manifest))
        self.assertTrue(os.path.exists(metadata))
        self.assertTrue(os.path.exists(diff))
        self.assertTrue(self._row_exists(snap_id))

        result = self._run_remove(cfg_path, snap_id)
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertFalse(os.path.exists(manifest))
        self.assertFalse(os.path.exists(metadata))
        self.assertFalse(os.path.exists(diff))
        self.assertFalse(self._row_exists(snap_id))

    def test_orphan_objects_are_gced_but_shared_objects_survive(self):
        repo, cfg_path = self._bootstrap()
        # Snapshot 1: feature.py contains "alpha", README.md contains "hello\n".
        snap_a = self._take_snapshot(
            repo, cfg_path, content="def f(): return 'alpha'\n"
        )
        # Snapshot 2: feature.py changed (new sha) but README.md still "hello\n"
        # which means README.md's object is shared between both snapshots.
        snap_b = self._take_snapshot(
            repo, cfg_path, content="def f(): return 'beta'\n"
        )

        objects_root = os.path.join(repo, ".ailine", "snapshots", "objects")
        all_objects_before = []
        for root, _, files in os.walk(objects_root):
            for f in files:
                all_objects_before.append(os.path.join(root, f))
        self.assertGreaterEqual(len(all_objects_before), 2)

        # Read manifest_a's shas: each file in snap_a's manifest is present.
        manifest_a = os.path.join(
            repo, ".ailine", "snapshots", f"{snap_a}.manifest.json"
        )
        with open(manifest_a) as f:
            entries_a = json.load(f)
        manifest_b = os.path.join(
            repo, ".ailine", "snapshots", f"{snap_b}.manifest.json"
        )
        with open(manifest_b) as f:
            entries_b = json.load(f)
        shas_a = {e["sha256"] for e in entries_a if "sha256" in e}
        shas_b = {e["sha256"] for e in entries_b if "sha256" in e}
        unique_to_a = shas_a - shas_b
        shared = shas_a & shas_b
        self.assertGreater(len(unique_to_a), 0, "test setup expects unique sha in A")
        self.assertGreater(len(shared), 0, "test setup expects shared sha")

        result = self._run_remove(cfg_path, snap_a)
        self.assertEqual(result.exit_code, 0, msg=result.output)

        for sha in unique_to_a:
            obj_path = os.path.join(objects_root, sha[:2], f"{sha}.zst")
            self.assertFalse(
                os.path.exists(obj_path),
                msg=f"unique object {sha} should be GCed",
            )
        for sha in shared:
            obj_path = os.path.join(objects_root, sha[:2], f"{sha}.zst")
            self.assertTrue(
                os.path.exists(obj_path),
                msg=f"shared object {sha} must survive",
            )

    def test_dry_run_makes_no_changes(self):
        repo, cfg_path = self._bootstrap()
        snap_id = self._take_snapshot(repo, cfg_path, content="x = 1\n")

        manifest = os.path.join(repo, ".ailine", "snapshots", f"{snap_id}.manifest.json")
        before_files = sorted(os.listdir(os.path.join(repo, ".ailine", "snapshots")))

        result = self._run_remove(cfg_path, snap_id, dry_run=True)
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("would remove", result.output)
        self.assertTrue(os.path.exists(manifest))
        self.assertTrue(self._row_exists(snap_id))
        after_files = sorted(os.listdir(os.path.join(repo, ".ailine", "snapshots")))
        self.assertEqual(before_files, after_files)

    def test_default_config_does_not_call_mlflow_delete_run(self):
        repo, cfg_path = self._bootstrap()
        snap_id = self._take_snapshot(repo, cfg_path, content="x = 1\n")
        _set_mlflow_run(constants.DB_PATH, snap_id, "abc123")

        client = MagicMock()
        with patch(
            "mlflow.tracking.MlflowClient", return_value=client, create=True
        ):
            result = self._run_remove(cfg_path, snap_id)
        self.assertEqual(result.exit_code, 0, msg=result.output)
        client.delete_run.assert_not_called()
        # Row deletion happens regardless of MLflow side.
        self.assertFalse(self._row_exists(snap_id))

    def test_yaml_default_with_mlflow_true_calls_delete_run(self):
        repo, cfg_path = self._bootstrap(with_mlflow_default=True)
        snap_id = self._take_snapshot(repo, cfg_path, content="x = 1\n")
        _set_mlflow_run(constants.DB_PATH, snap_id, "abc123")

        client = MagicMock()
        with patch(
            "mlflow.tracking.MlflowClient", return_value=client, create=True
        ):
            result = self._run_remove(cfg_path, snap_id)
        self.assertEqual(result.exit_code, 0, msg=result.output)
        client.delete_run.assert_called_once_with("abc123")

    def test_cli_overrides_yaml_with_false(self):
        repo, cfg_path = self._bootstrap(with_mlflow_default=True)
        snap_id = self._take_snapshot(repo, cfg_path, content="x = 1\n")
        _set_mlflow_run(constants.DB_PATH, snap_id, "abc123")

        client = MagicMock()
        with patch(
            "mlflow.tracking.MlflowClient", return_value=client, create=True
        ):
            result = self._run_remove(cfg_path, snap_id, with_mlflow="false")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        client.delete_run.assert_not_called()

    def test_cli_overrides_yaml_with_true(self):
        repo, cfg_path = self._bootstrap(with_mlflow_default=False)
        snap_id = self._take_snapshot(repo, cfg_path, content="x = 1\n")
        _set_mlflow_run(constants.DB_PATH, snap_id, "abc123")

        client = MagicMock()
        with patch(
            "mlflow.tracking.MlflowClient", return_value=client, create=True
        ):
            result = self._run_remove(cfg_path, snap_id, with_mlflow="true")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        client.delete_run.assert_called_once_with("abc123")

    def test_with_mlflow_true_no_run_id_is_noop(self):
        repo, cfg_path = self._bootstrap()
        snap_id = self._take_snapshot(repo, cfg_path, content="x = 1\n")
        _set_mlflow_run(constants.DB_PATH, snap_id, None)

        client = MagicMock()
        with patch(
            "mlflow.tracking.MlflowClient", return_value=client, create=True
        ):
            result = self._run_remove(cfg_path, snap_id, with_mlflow="true")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        client.delete_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
