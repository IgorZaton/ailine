import unittest
from unittest.mock import MagicMock, patch

from ailine.config import constants
from ailine.integrations.mlflow_links import get_mlflow_run_browser_context, run_detail_url


class MlflowUiLinkTests(unittest.TestCase):
    def test_run_detail_url_uses_mlflow_ui_base(self):
        old = constants.MLFLOW_UI_BASE
        try:
            constants.MLFLOW_UI_BASE = "http://mlflow.test:5001"
            self.assertEqual(
                run_detail_url("3", "run-abc"),
                "http://mlflow.test:5001/#/experiments/3/runs/run-abc",
            )
        finally:
            constants.MLFLOW_UI_BASE = old

    @patch("ailine.integrations.mlflow_links.mlflow.get_run")
    def test_browser_context_returns_url_and_display_name(self, mock_get_run):
        run = MagicMock()
        run.info.experiment_id = "7"
        run.data.tags = {"mlflow.runName": "my-training-run"}
        mock_get_run.return_value = run
        url, name = get_mlflow_run_browser_context("rid-1")
        self.assertIn("#/experiments/7/runs/rid-1", url or "")
        self.assertEqual(name, "my-training-run")


if __name__ == "__main__":
    unittest.main()
