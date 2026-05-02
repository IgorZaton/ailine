import unittest

from ailine.integrations.mlflow_links import run_detail_url
from ailine.config import constants


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


if __name__ == "__main__":
    unittest.main()
