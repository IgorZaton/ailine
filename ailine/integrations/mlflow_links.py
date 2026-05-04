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


def get_mlflow_run_browser_context(run_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Return ``(detail_page_url, run_display_name)`` for a stored ``run_id``.

    Uses a single ``mlflow.get_run`` call. ``run_display_name`` is the
    ``mlflow.runName`` tag when present (same string AIline passes to
    ``start_run(run_name=...)`` in wrap mode when names are aligned).
    On failure returns ``(None, None)`` and logs at debug.
    """
    if not run_id or not str(run_id).strip():
        return None, None
    rid = str(run_id).strip()
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        run = mlflow.get_run(rid)
        url = run_detail_url(run.info.experiment_id, rid)
        tags = getattr(run.data, "tags", None) or {}
        display = tags.get("mlflow.runName")
        return url, display
    except Exception as exc:
        logging.debug("MLflow run lookup unavailable for %s: %s", rid, exc)
        return None, None


def resolve_mlflow_ui_url(run_id: Optional[str]) -> Optional[str]:
    """Best-effort MLflow UI URL for ``run_id`` (see :func:`get_mlflow_run_browser_context`)."""
    url, _ = get_mlflow_run_browser_context(run_id)
    return url
