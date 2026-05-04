"""Integration tests for ``run_tracked_command`` (session orchestrator)."""

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from ailine.config import constants
from ailine.config.validate import validate_config
from ailine.persistence.db import init_db
from ailine.run.session import SessionError, run_tracked_command
from ailine.snapshot.storage import resolve_storage_dir


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


class TrackedCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _bootstrap_repo(self.tmp.name)
        self.storage = os.path.join(self.tmp.name, "snapshots")

        # Isolated SQLite DB for this test.
        self.original_db = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "tree.db")
        init_db()
        self.addCleanup(self._restore_db)

        self.cfg_path = os.path.join(self.repo, ".ailine.yml")
        self.config = validate_config(self.cfg_path)

    def _restore_db(self):
        constants.DB_PATH = self.original_db

    def _read_run_rows(self):
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT id, type, run_command_summary, run_command_json, record_name "
                "FROM tree"
            ).fetchall()
        finally:
            conn.close()
        return rows

    def test_clean_repo_records_git_commit_and_argv(self):
        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print('ok')"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.commit_type, "git")

        rows = self._read_run_rows()
        self.assertEqual(len(rows), 1)
        rid, rtype, summary, payload_json, name = rows[0]
        self.assertEqual(rtype, "git")
        self.assertIn("print('ok')", summary)
        self.assertIsNotNone(name)
        self.assertRegex(name, r"^[a-z0-9]+-[a-z0-9]+$")
        self.assertEqual(name, result.record_name)
        payload = json.loads(payload_json)
        self.assertEqual(payload["argv"][:2], [sys.executable, "-c"])
        self.assertEqual(payload["cwd"], self.repo)

    def test_custom_record_name(self):
        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print(2)"],
            storage=self.storage,
            config=self.config,
            record_name="exp-baseline-A",
        )
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.record_name, "exp-baseline-A")
        rows = self._read_run_rows()
        self.assertEqual(rows[0][4], "exp-baseline-A")

    def test_dirty_tree_creates_snapshot(self):
        with open(os.path.join(self.repo, "new_file.txt"), "w") as f:
            f.write("uncommitted\n")

        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print(1)"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.commit_type, "snapshot")
        # objects-v1 snapshots intentionally do not produce a per-snapshot payload.
        self.assertIsNone(result.snapshot_path)

        snap_id = result.commit_id
        manifest_file = os.path.join(self.storage, f"{snap_id}.manifest.json")
        metadata_file = os.path.join(self.storage, f"{snap_id}.metadata.json")
        diff_file = os.path.join(self.storage, f"{snap_id}.diff.patch")
        self.assertTrue(os.path.exists(manifest_file), msg=manifest_file)
        self.assertTrue(os.path.exists(metadata_file), msg=metadata_file)
        self.assertTrue(os.path.exists(diff_file), msg=diff_file)

        with open(metadata_file, "r", encoding="utf-8") as f:
            meta = json.load(f)
        self.assertEqual(meta["format"], "objects-v1")
        self.assertNotIn("archive_path", meta)

        objects_root = os.path.join(self.storage, "objects")
        self.assertTrue(os.path.isdir(objects_root))
        stored = []
        for shard in os.listdir(objects_root):
            for name in os.listdir(os.path.join(objects_root, shard)):
                stored.append(name)
        self.assertGreaterEqual(len(stored), 1)

    def test_dirty_tree_dedups_objects_across_runs(self):
        with open(os.path.join(self.repo, "shared.txt"), "w") as f:
            f.write("shared content\n")

        run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print(1)"],
            storage=self.storage,
            config=self.config,
        )

        objects_root = os.path.join(self.storage, "objects")
        first_count = sum(
            len(os.listdir(os.path.join(objects_root, shard)))
            for shard in os.listdir(objects_root)
        )
        self.assertGreater(first_count, 0)

        # Add a second untracked file but keep ``shared.txt`` byte-identical.
        with open(os.path.join(self.repo, "second.txt"), "w") as f:
            f.write("only in second snapshot\n")

        run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print(2)"],
            storage=self.storage,
            config=self.config,
        )

        second_count = sum(
            len(os.listdir(os.path.join(objects_root, shard)))
            for shard in os.listdir(objects_root)
        )
        # The second snapshot only adds ``second.txt``; every other included
        # file was byte-identical and must be deduped against the first run.
        self.assertEqual(
            second_count,
            first_count + 1,
            msg=f"expected exactly +1 object after second run (first={first_count}, second={second_count})",
        )

    def test_inherit_mode_does_not_open_outer_mlflow_run(self):
        with patch("ailine.run.session.mlflow.start_run") as start_run, patch(
            "ailine.run.session.mlflow.search_runs", return_value=None
        ):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=self.config,  # default mlflow.mode == 'inherit'
            )
            start_run.assert_not_called()

    def test_inherit_mode_records_mlflow_run_when_post_hoc_lookup_succeeds(self):
        # Disable inherit-mode pre-link so the post-hoc search is the path
        # under test here.
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["prelink"] = False
        with patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value="post-hoc-run-id",
        ) as mock_lookup:
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )
        mock_lookup.assert_called_once()
        self.assertEqual(result.mlflow_run_id, "post-hoc-run-id")

    def test_inherit_auto_syncs_probably_auto_mlflow_name(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "inherit"
        cfg.track["mlflow"]["inherit_name_sync"] = "auto"
        cfg.track["mlflow"]["prelink"] = False
        with patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value="post-hoc-run-id",
        ), patch("ailine.run.session.MlflowClient") as client_cls:
            client = client_cls.return_value
            client.get_run.return_value.data.tags = {"mlflow.runName": "calm-panda"}
            result = run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
                record_name="aligned-name",
            )
        self.assertEqual(result.mlflow_run_id, "post-hoc-run-id")
        client.set_tag.assert_called_once_with(
            "post-hoc-run-id", "mlflow.runName", "aligned-name"
        )

    def test_inherit_auto_does_not_override_custom_mlflow_name(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "inherit"
        cfg.track["mlflow"]["inherit_name_sync"] = "auto"
        cfg.track["mlflow"]["prelink"] = False
        with patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value="post-hoc-run-id",
        ), patch("ailine.run.session.MlflowClient") as client_cls:
            client = client_cls.return_value
            client.get_run.return_value.data.tags = {"mlflow.runName": "train_dummy"}
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
                record_name="aligned-name",
            )
        client.set_tag.assert_not_called()

    def test_inherit_force_always_syncs_mlflow_name(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "inherit"
        cfg.track["mlflow"]["inherit_name_sync"] = "force"
        cfg.track["mlflow"]["prelink"] = False
        with patch(
            "ailine.run.session._best_effort_mlflow_run_after_inherit_child",
            return_value="post-hoc-run-id",
        ), patch("ailine.run.session.MlflowClient") as client_cls:
            client = client_cls.return_value
            client.get_run.return_value.data.tags = {"mlflow.runName": "train_dummy"}
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
                record_name="aligned-name",
            )
        client.set_tag.assert_called_once_with(
            "post-hoc-run-id", "mlflow.runName", "aligned-name"
        )

    def test_wrap_mode_opens_outer_mlflow_run(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "wrap"
        with patch("ailine.run.session.mlflow.start_run") as start_run:
            ctx = start_run.return_value
            ctx.__enter__.return_value = None
            ctx.__exit__.return_value = False
            with patch(
                "ailine.run.session.mlflow.active_run"
            ) as active_run:
                active_run.return_value.info.run_id = "run-123"
                result = run_tracked_command(
                    git_root=self.repo,
                    argv=[sys.executable, "-c", "print(1)"],
                    storage=self.storage,
                    config=cfg,
                )
            start_run.assert_called_once()
            self.assertEqual(result.mlflow_run_id, "run-123")
            self.assertEqual(start_run.call_args.kwargs["run_name"], result.record_name)

    def test_run_name_only_used_for_db_and_mlflow_wrap(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "wrap"
        with patch("ailine.run.session.mlflow.start_run") as start_run:
            ctx = start_run.return_value
            ctx.__enter__.return_value = None
            ctx.__exit__.return_value = False
            with patch("ailine.run.session.mlflow.active_run") as active_run:
                active_run.return_value.info.run_id = "run-z"
                result = run_tracked_command(
                    git_root=self.repo,
                    argv=[sys.executable, "-c", "print(1)"],
                    storage=self.storage,
                    config=cfg,
                    run_name="only-cli-run",
                )
        self.assertEqual(result.record_name, "only-cli-run")
        self.assertEqual(start_run.call_args.kwargs["run_name"], "only-cli-run")
        rows = self._read_run_rows()
        self.assertEqual(rows[0][4], "only-cli-run")

    def test_wrap_mode_split_name_and_run_name(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["mlflow"]["mode"] = "wrap"
        with patch("ailine.run.session.mlflow.start_run") as start_run:
            ctx = start_run.return_value
            ctx.__enter__.return_value = None
            ctx.__exit__.return_value = False
            with patch("ailine.run.session.mlflow.active_run") as active_run:
                active_run.return_value.info.run_id = "run-id"
                result = run_tracked_command(
                    git_root=self.repo,
                    argv=[sys.executable, "-c", "print(1)"],
                    storage=self.storage,
                    config=cfg,
                    record_name="db-nice",
                    run_name="mlflow-metric",
                )
        self.assertEqual(result.record_name, "db-nice")
        self.assertEqual(start_run.call_args.kwargs["run_name"], "mlflow-metric")
        rows = self._read_run_rows()
        self.assertEqual(rows[0][4], "db-nice")

    def test_dvc_verify_strict_aborts_on_failing_command(self):
        cfg = validate_config(self.cfg_path)
        cfg.track["dvc"]["verify"] = "strict"
        # `false` exits 1 reliably on POSIX shells.
        cfg.track["dvc"]["verify_commands"] = [["false"]]
        with self.assertRaises(SessionError):
            run_tracked_command(
                git_root=self.repo,
                argv=[sys.executable, "-c", "print(1)"],
                storage=self.storage,
                config=cfg,
            )

    def test_child_nonzero_exit_propagates(self):
        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "import sys; sys.exit(7)"],
            storage=self.storage,
            config=self.config,
        )
        self.assertEqual(result.exit_code, 7)

    def test_empty_argv_raises(self):
        with self.assertRaises(SessionError):
            run_tracked_command(
                git_root=self.repo,
                argv=[],
                storage=self.storage,
                config=self.config,
            )

    def test_resolved_storage_dir_from_yaml_is_used(self):
        rel_path = "custom-snaps"
        cfg = validate_config(self.cfg_path)
        cfg.snapshot["storage_dir"] = rel_path
        with open(os.path.join(self.repo, "uncommitted.txt"), "w") as f:
            f.write("dirty\n")

        resolved = resolve_storage_dir(cfg.snapshot, self.repo)
        self.assertEqual(resolved, os.path.join(self.repo, rel_path))

        result = run_tracked_command(
            git_root=self.repo,
            argv=[sys.executable, "-c", "print(0)"],
            storage=resolved,
            config=cfg,
        )
        self.assertEqual(result.commit_type, "snapshot")
        self.assertTrue(
            os.path.isdir(os.path.join(resolved, "objects")),
            msg=f"objects dir missing under {resolved}",
        )

    def test_non_executable_py_script_raises_session_error_with_hint(self):
        script_path = os.path.join(self.repo, "side.py")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write("print('x')\n")
        subprocess.run(["git", "-C", self.repo, "add", "side.py"], check=True)
        subprocess.run(
            ["git", "-C", self.repo, "commit", "-q", "-m", "add side script"],
            check=True,
        )
        with self.assertRaises(SessionError) as ctx:
            run_tracked_command(
                git_root=self.repo,
                argv=["side.py"],
                storage=self.storage,
                config=self.config,
            )
        msg = str(ctx.exception).lower()
        self.assertIn("interpreter", msg)
        self.assertIn("python", msg)


if __name__ == "__main__":
    unittest.main()
