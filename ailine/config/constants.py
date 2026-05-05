"""Runtime path/URL constants resolved from environment variables.

These are looked up at import time, with environment defaults appropriate for
running ailine from a project root. Tests can override individual constants by
mutating attributes on this module (e.g. ``constants.DB_PATH = "..."``).
"""

import os
from pathlib import Path
from urllib.parse import urlparse

DB_PATH = os.environ.get("AILINE_DB_PATH", "ailine_tree.db")
CONFIG_PATH = os.environ.get("AILINE_CONFIG_PATH", "ailine_config.txt")
REPO_DIR = os.environ.get("AILINE_REPO_DIR", "repo")

# Local file-backed store by default so ``ailine run`` works without a tracking server.
# Override with ``AILINE_MLFLOW_URI=http://localhost:5001`` if you run ``mlflow ui`` /
# ``ailine serve`` and want the REST API on port 5001.
_MLFLOW_STORAGE = Path(os.environ.get("AILINE_MLFLOW_STORAGE", os.path.abspath("mlruns"))).resolve()
MLFLOW_STORAGE_DIR = str(_MLFLOW_STORAGE)
MLFLOW_TRACKING_URI = os.environ.get("AILINE_MLFLOW_URI", _MLFLOW_STORAGE.as_uri())


def _resolve_mlflow_ui_base() -> str:
    """Base URL for MLflow's **browser UI** (used for Run ID links in ailine web).

    Not the same as ``MLFLOW_TRACKING_URI`` when tracking is ``file:``-backed.
    """
    explicit = os.environ.get("AILINE_MLFLOW_UI_BASE")
    if explicit:
        return explicit.rstrip("/")
    # If user points tracking at an http(s) server, assume the UI is on the same origin.
    tr = os.environ.get("AILINE_MLFLOW_URI")
    if tr and tr.startswith(("http://", "https://")):
        p = urlparse(tr)
        if p.netloc:
            return f"{p.scheme}://{p.netloc}".rstrip("/")
    return "http://127.0.0.1:5001"


MLFLOW_UI_BASE = _resolve_mlflow_ui_base()
LOG_PATH = os.environ.get("AILINE_LOG_PATH", "ailine.log")
POLICY_PATH = os.environ.get("AILINE_POLICY_PATH", ".ailine.yml")
STATE_DIR = os.environ.get("AILINE_STATE_DIR", ".ailine")
DEFAULT_STORAGE_DIR = os.environ.get(
    "AILINE_STORAGE_DIR", os.path.abspath(os.path.join(STATE_DIR, "snapshots"))
)
LARGE_FILE_POLICY_STORE = os.path.join(STATE_DIR, "large-file-policy.json")
OBJECT_STORE_DIR = os.path.join(STATE_DIR, "objects")
POINTER_STORE_DIR = os.path.join(STATE_DIR, "pointers")
