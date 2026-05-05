"""Runtime path/URL constants resolved from environment variables.

These are looked up at import time, with environment defaults appropriate for
running ailine from a project root. Tests can override individual constants by
mutating attributes on this module (e.g. ``constants.DB_PATH = "..."``).
"""

import os
from pathlib import Path
from typing import Tuple
from urllib.parse import urlparse

REPO_DIR = os.environ.get("AILINE_REPO_DIR", "repo")

# Local file-backed store by default so ``ailine run`` works without a tracking server.
# Override with ``AILINE_MLFLOW_URI=http://localhost:5001`` (or the standard
# ``MLFLOW_TRACKING_URI`` env var) if you run a tracking server.
_MLFLOW_STORAGE = Path(os.environ.get("AILINE_MLFLOW_STORAGE", os.path.abspath("mlruns"))).resolve()
MLFLOW_STORAGE_DIR = str(_MLFLOW_STORAGE)


def _resolve_mlflow_tracking_uri() -> Tuple[str, str]:
    """Resolve the MLflow tracking URI and the source label that produced it.

    Resolution order (first match wins):

    1. ``AILINE_MLFLOW_URI`` - explicit AIline override.
    2. ``MLFLOW_TRACKING_URI`` - standard MLflow env var (so AIline aligns
       with whatever the user already exported for their training scripts).
    3. ``file://<cwd>/mlruns`` - local file backend default.
    """
    ailine_override = os.environ.get("AILINE_MLFLOW_URI")
    if ailine_override:
        return ailine_override, "AILINE_MLFLOW_URI"
    mlflow_env = os.environ.get("MLFLOW_TRACKING_URI")
    if mlflow_env:
        return mlflow_env, "MLFLOW_TRACKING_URI"
    return _MLFLOW_STORAGE.as_uri(), "default(file://mlruns)"


def _resolve_mlflow_ui_base() -> Tuple[str, str]:
    """Base URL for MLflow's **browser UI** (and the source label).

    Not the same as ``MLFLOW_TRACKING_URI`` when tracking is ``file:``-backed.
    Honors both ``AILINE_MLFLOW_URI`` and ``MLFLOW_TRACKING_URI`` when
    deriving the origin from an http(s) tracking URL.
    """
    explicit = os.environ.get("AILINE_MLFLOW_UI_BASE")
    if explicit:
        return explicit.rstrip("/"), "AILINE_MLFLOW_UI_BASE"
    for env_name in ("AILINE_MLFLOW_URI", "MLFLOW_TRACKING_URI"):
        tr = os.environ.get(env_name)
        if tr and tr.startswith(("http://", "https://")):
            p = urlparse(tr)
            if p.netloc:
                return f"{p.scheme}://{p.netloc}".rstrip("/"), f"derived({env_name})"
    return "http://127.0.0.1:5001", "default(http://127.0.0.1:5001)"


_TRACKING_URI, _TRACKING_URI_SOURCE = _resolve_mlflow_tracking_uri()
MLFLOW_TRACKING_URI = _TRACKING_URI

_UI_BASE, _UI_BASE_SOURCE = _resolve_mlflow_ui_base()
MLFLOW_UI_BASE = _UI_BASE
POLICY_PATH = os.environ.get("AILINE_POLICY_PATH", ".ailine.yml")
STATE_DIR = os.environ.get("AILINE_STATE_DIR", ".ailine")

# AIline's own auto-generated artifacts (DB, log, demo bookkeeping) live under
# ``.ailine/`` so the project root stays clean. User-controlled paths
# (``mlruns/``, ``repo/``, ``.ailine.yml``, ``.ailineignore``) are not moved.
DB_PATH = os.environ.get("AILINE_DB_PATH", os.path.join(STATE_DIR, "tree.db"))
LOG_PATH = os.environ.get("AILINE_LOG_PATH", os.path.join(STATE_DIR, "ailine.log"))
CONFIG_PATH = os.environ.get(
    "AILINE_CONFIG_PATH", os.path.join(STATE_DIR, "demo-config.txt")
)

_DEFAULT_STORAGE_OVERRIDE = os.environ.get("AILINE_STORAGE_DIR")
DEFAULT_STORAGE_DIR = (
    _DEFAULT_STORAGE_OVERRIDE
    if _DEFAULT_STORAGE_OVERRIDE
    else os.path.abspath(os.path.join(STATE_DIR, "snapshots"))
)
_DEFAULT_STORAGE_DIR_SOURCE = (
    "AILINE_STORAGE_DIR" if _DEFAULT_STORAGE_OVERRIDE else f"default({STATE_DIR}/snapshots)"
)
LARGE_FILE_POLICY_STORE = os.path.join(STATE_DIR, "large-file-policy.json")
OBJECT_STORE_DIR = os.path.join(STATE_DIR, "objects")
POINTER_STORE_DIR = os.path.join(STATE_DIR, "pointers")


def resolve_mlflow_environment() -> dict:
    """Re-resolve effective MLflow + storage runtime values with provenance.

    Reads the live process environment (not the values frozen at import time)
    so callers like ``ailine init-workspace`` see the user's current shell.
    """
    tracking_uri, tracking_source = _resolve_mlflow_tracking_uri()
    ui_base, ui_source = _resolve_mlflow_ui_base()

    storage_override = os.environ.get("AILINE_STORAGE_DIR")
    if storage_override:
        storage_dir = storage_override
        storage_source = "AILINE_STORAGE_DIR"
    else:
        storage_dir = os.path.abspath(os.path.join(STATE_DIR, "snapshots"))
        storage_source = f"default({STATE_DIR}/snapshots)"

    return {
        "tracking_uri": tracking_uri,
        "tracking_uri_source": tracking_source,
        "ui_base": ui_base,
        "ui_base_source": ui_source,
        "storage_dir": storage_dir,
        "storage_dir_source": storage_source,
    }

# Mapping from legacy (project-root) defaults to the new ``.ailine/`` defaults.
# Used by :func:`ailine.run.migration.migrate_legacy_state_artifacts` to move
# existing artifacts on first invocation under the new layout. We keep this
# here (next to the new defaults) so the two stay in lockstep.
LEGACY_STATE_ARTIFACT_MAP = (
    ("ailine_tree.db", os.path.join(STATE_DIR, "tree.db")),
    ("ailine.log", os.path.join(STATE_DIR, "ailine.log")),
    ("ailine_config.txt", os.path.join(STATE_DIR, "demo-config.txt")),
)
