import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import ailine
from ailine.config import constants


def _make_runs_df(records):
    return pd.DataFrame.from_records(records)


class _FakeMlflowRun:
    def __init__(self, experiment_id):
        self.info = type("Info", (), {"experiment_id": experiment_id})()


class LineagePageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "lineage.db")
        ailine.init_db()
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO tree (id, type, mlflow_run, timestamp, "
                "run_command_summary, dvc_linkage_status, env_fingerprint_status, "
                "git_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "abc1234",
                    "git",
                    "linked-run-1",
                    "2026-05-03T00:00:00",
                    "python train.py",
                    "ok",
                    "ok",
                    "https://example.com/commit/abc1234",
                ),
            )
            conn.commit()
        finally:
            conn.close()
        self.client = ailine.app.test_client()

    def tearDown(self):
        constants.DB_PATH = self._old_db_path

    @patch("ailine.web.routes.lineage.mlflow.search_runs")
    @patch("ailine.integrations.mlflow_links.mlflow.get_run")
    def test_root_renders_main_table_with_resolved_mlflow_url(
        self, mock_get_run, mock_search_runs
    ):
        mock_get_run.return_value = _FakeMlflowRun(experiment_id="42")
        mock_search_runs.return_value = _make_runs_df([])

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Lineage", body)
        self.assertIn("abc1234", body)
        self.assertIn("python train.py", body)
        expected_url_fragment = (
            constants.MLFLOW_UI_BASE.rstrip("/") + "/#/experiments/42/runs/linked-run-1"
        )
        self.assertIn(expected_url_fragment, body)
        mock_get_run.assert_called_with("linked-run-1")

    @patch("ailine.web.routes.lineage.mlflow.search_runs")
    @patch("ailine.integrations.mlflow_links.mlflow.get_run")
    def test_orphan_runs_are_filtered_and_listed(self, mock_get_run, mock_search_runs):
        def _get_run(rid):
            return _FakeMlflowRun(experiment_id="9" if rid == "orphan-run-1" else "42")

        mock_get_run.side_effect = _get_run
        mock_search_runs.return_value = _make_runs_df(
            [
                {
                    "run_id": "linked-run-1",
                    "experiment_id": "42",
                    "start_time": "2026-05-03T00:00:00",
                    "tags.commit": "abc1234",
                    "tags.snapshot": None,
                },
                {
                    "run_id": "orphan-run-1",
                    "experiment_id": "9",
                    "start_time": "2026-05-03T00:00:01",
                    "tags.commit": None,
                    "tags.snapshot": None,
                },
            ]
        )

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("Unlinked MLflow runs (1)", body)
        self.assertIn("orphan-run-1", body)

    @patch("ailine.web.routes.lineage.mlflow.search_runs")
    @patch("ailine.integrations.mlflow_links.mlflow.get_run")
    def test_unresolvable_mlflow_run_renders_id_without_link(
        self, mock_get_run, mock_search_runs
    ):
        mock_get_run.side_effect = RuntimeError("mlflow store unreachable")
        mock_search_runs.side_effect = RuntimeError("mlflow store unreachable")

        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        body = response.data.decode("utf-8")
        self.assertIn("linked-run-1", body)
        self.assertNotIn("/#/experiments/", body)
        self.assertIn("Unlinked MLflow runs (0)", body)


class LegacyRedirectTests(unittest.TestCase):
    def setUp(self):
        self.client = ailine.app.test_client()

    def test_commits_redirects_to_root(self):
        response = self.client.get("/commits", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/"),
            f"unexpected Location: {response.headers['Location']}",
        )

    def test_experiments_redirects_to_root(self):
        response = self.client.get("/experiments", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            response.headers["Location"].endswith("/"),
            f"unexpected Location: {response.headers['Location']}",
        )


if __name__ == "__main__":
    unittest.main()
