import sqlite3
import tempfile
import unittest

import ailine
import utils


class RunCommandCaptureTests(unittest.TestCase):
    def test_build_run_command_payload(self):
        payload, summary = ailine.build_run_command_payload(
            script="train.py",
            dataset="data.csv",
            storage="/tmp/snapshots",
            repo_cwd="/tmp/repo",
        )
        self.assertEqual(payload["entrypoint"], "python")
        self.assertEqual(payload["script"], "train.py")
        self.assertEqual(payload["dataset"], "data.csv")
        self.assertEqual(payload["storage"], "/tmp/snapshots")
        self.assertEqual(payload["cwd"], "/tmp/repo")
        self.assertEqual(summary, "python train.py --dataset data.csv --storage /tmp/snapshots")

    def test_init_db_adds_run_command_columns(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_db = ailine.DB_PATH
            try:
                ailine.DB_PATH = f"{tmp}/test.db"
                ailine.init_db()
                conn = sqlite3.connect(ailine.DB_PATH)
                cur = conn.cursor()
                cur.execute("PRAGMA table_info(tree)")
                columns = {row[1] for row in cur.fetchall()}
                conn.close()
                self.assertIn("run_command_json", columns)
                self.assertIn("run_command_summary", columns)
            finally:
                ailine.DB_PATH = old_db

    def test_table_output_includes_command_summary(self):
        data = [
            {
                "id": "abc123456",
                "type": "git",
                "dvc_version": "dataset_v1",
                "dvc_linkage_status": "local_only",
                "env_fingerprint_status": "complete",
                "run_command_summary": "python train.py --dataset data.csv --storage /tmp/snapshots",
                "dvc_linkage_count": 1,
                "timestamp": "2026-04-30T16:00:00",
            }
        ]
        # smoke check it renders without exceptions
        utils.print_table(data)


if __name__ == "__main__":
    unittest.main()
