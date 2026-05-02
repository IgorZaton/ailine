"""``/experiments`` route — surface MLflow runs alongside ailine commits."""

import logging

import mlflow
from flask import Flask, render_template

from ailine.config import constants
from ailine.integrations.mlflow_links import run_detail_url
from ailine.web.state import get_repo_url, load_repo_url


def view():
    load_repo_url()
    mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
    try:
        runs = mlflow.search_runs()
        runs_data = [
            {
                "run_id": r["run_id"],
                "mlflow_url": run_detail_url(r["experiment_id"], r["run_id"]),
                "accuracy": r.get("metrics.accuracy", "N/A"),
                "commit": r.get("tags.commit"),
                "snapshot": r.get("tags.snapshot"),
                "dataset": r.get("tags.dataset", "N/A"),
                "timestamp": r.get("info.start_time", "N/A"),
            }
            for r in runs.to_dict(orient="records")
        ]
        logging.info(f"Experiments page accessed, found {len(runs_data)} runs")
        return render_template("experiments.html", runs=runs_data, repo_url=get_repo_url())
    except Exception as e:
        logging.error(f"Error in experiments route: {str(e)}")
        return f"Internal Server Error: {str(e)}", 500


def register(app: Flask) -> None:
    app.add_url_rule("/experiments", endpoint="experiments", view_func=view)
