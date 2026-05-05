"""AIline: ML experiment lineage tracker with snapshot-based reproducibility.

The legacy public surface (``ailine.scan_repo_files``, ``ailine.app``, etc.) is
re-exported here so tests and external callers continue to work after the
package split. New code should prefer the explicit submodule paths
(``ailine.snapshot.scan``, ``ailine.web.app``, ...).
"""

from importlib import metadata as _metadata

try:
    __version__ = _metadata.version("ailine")
except _metadata.PackageNotFoundError:
    __version__ = "0.1.0"

# Public re-exports (kept stable for tests and back-compat).
from ailine.config.constants import (  # noqa: F401
    DB_PATH,
    CONFIG_PATH,
    REPO_DIR,
    MLFLOW_TRACKING_URI,
    MLFLOW_UI_BASE,
    MLFLOW_STORAGE_DIR,
    LOG_PATH,
    DEFAULT_STORAGE_DIR,
    POLICY_PATH,
    STATE_DIR,
    LARGE_FILE_POLICY_STORE,
    OBJECT_STORE_DIR,
    POINTER_STORE_DIR,
)
from ailine.config.defaults import (  # noqa: F401
    CommitType,
    DEFAULT_DVC_CONFIG,
    DEFAULT_ENVIRONMENT_CONFIG,
    DEFAULT_RUN_CAPTURE_CONFIG,
    DEFAULT_SNAPSHOT_POLICY,
)
from ailine.fingerprint.env import collect_environment_fingerprint  # noqa: F401
from ailine.linkage.dvc import (  # noqa: F401
    build_dvc_linkage,
    discover_dvc_outputs,
    get_dvc_remote_info,
)
from ailine.persistence.db import init_db  # noqa: F401
from ailine.run.capture import build_run_command_payload  # noqa: F401
from ailine.snapshot.archive import create_snapshot  # noqa: F401
from ailine.snapshot.manifest import build_manifest  # noqa: F401
from ailine.snapshot.paths import (  # noqa: F401
    ensure_utf8_text,
    is_excluded,
    normalize_rel_path,
    sha256_file,
)
from ailine.snapshot.scan import (  # noqa: F401
    create_large_file_pointer,
    discover_dvc_tracked_paths,
    resolve_large_file_decisions,
    scan_repo_files,
)
from ailine.web.app import app  # noqa: F401
# Re-exports needed by tests that patch these attributes on the ``ailine`` module.
from ailine.web.routes import commit_view as _commit_view_module  # noqa: F401
from flask import render_template  # noqa: F401
import git  # noqa: F401
