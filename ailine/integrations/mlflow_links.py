"""Browser-openable URLs for MLflow UI (distinct from ``MLFLOW_TRACKING_URI``).

The tracking URI may be ``file:///...`` for local runs; the MLflow **web UI**
is always served over HTTP. Use `AILINE_MLFLOW_UI_BASE` (or infer from an
http(s) tracking URI) when building links in the ailine Flask app.
"""

from ailine.config import constants


def run_detail_url(experiment_id: str, run_id: str) -> str:
    base = constants.MLFLOW_UI_BASE.rstrip("/")
    return f"{base}/#/experiments/{experiment_id}/runs/{run_id}"
