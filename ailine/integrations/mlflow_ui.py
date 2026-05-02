"""Spawn / tear down a local MLflow UI process tied to the ailine session."""

import atexit
import logging
import subprocess
import time
from typing import Optional

from ailine.config import constants


_MLFLOW_PROCESS: Optional[subprocess.Popen] = None


def start_mlflow_ui() -> None:
    global _MLFLOW_PROCESS
    if _MLFLOW_PROCESS is not None:
        return
    cmd = [
        "mlflow",
        "ui",
        "--backend-store-uri",
        constants.MLFLOW_STORAGE_DIR,
        "--host",
        "0.0.0.0",
        "--port",
        "5001",
    ]
    try:
        _MLFLOW_PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logging.info(f"Started MLflow UI on port 5001 with PID {_MLFLOW_PROCESS.pid}")
        time.sleep(2)
        atexit.register(cleanup_mlflow_ui)
    except Exception as e:
        logging.error(f"Failed to start MLflow UI: {str(e)}")
        raise


def cleanup_mlflow_ui() -> None:
    global _MLFLOW_PROCESS
    if _MLFLOW_PROCESS is None:
        return
    pid = _MLFLOW_PROCESS.pid
    _MLFLOW_PROCESS.terminate()
    _MLFLOW_PROCESS.wait()
    logging.info(f"Terminated MLflow UI with PID {pid}")
    _MLFLOW_PROCESS = None
