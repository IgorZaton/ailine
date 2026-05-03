"""Browser-openable URLs for MLflow UI (distinct from ``MLFLOW_TRACKING_URI``).

The tracking URI may be ``file:///...`` for local runs; the MLflow **web UI**
is always served over HTTP. Use `AILINE_MLFLOW_UI_BASE` (or infer from an
http(s) tracking URI) when building links in the ailine Flask app.
"""

import logging
from typing import Optional

import mlflow

from ailine.config import constants


def run_detail_url(experiment_id: str, run_id: str) -> str:
    base = constants.MLFLOW_UI_BASE.rstrip("/")
    return f"{base}/#/experiments/{experiment_id}/runs/{run_id}"


def resolve_mlflow_ui_url(run_id: Optional[str]) -> Optional[str]:
    """Best-effort lookup of the MLflow UI URL for a stored ``run_id``.

    Calls ``mlflow.get_run`` to obtain the experiment id, then composes a
    browser URL via :func:`run_detail_url`. Returns ``None`` (and logs at
    debug) on any failure so the caller can render the raw id without a link.
    """
    if not run_id or not str(run_id).strip():
        return None
    rid = str(run_id).strip()
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        run = mlflow.get_run(rid)
        return run_detail_url(run.info.experiment_id, rid)
    except Exception as exc:
        logging.debug("MLflow UI URL unavailable for run %s: %s", rid, exc)
        return None
