"""Default configuration values and shared enums."""

from enum import Enum


DEFAULT_SNAPSHOT_POLICY = {
    "exclude_globs": [
        ".git/**",
        ".dvc/cache/**",
        ".venv/**",
        "__pycache__/**",
        "*.pyc",
        "*.pyo",
        ".pytest_cache/**",
        ".mypy_cache/**",
        "mlruns/**",
        ".ailine/**",
    ],
    "large_file_mb": 50,
    "large_file_mode": "prompt",
    "dvc_pointer_patterns": ["*.dvc"],
    # Where snapshot bundles + content-addressed objects live. ``None`` -> resolved at
    # runtime via ``AILINE_STORAGE_DIR`` env or ``constants.DEFAULT_STORAGE_DIR``.
    # Relative values are resolved against the repo root, not cwd.
    "storage_dir": None,
}

DEFAULT_DVC_CONFIG = {
    "remote_name": None,
    "require_hash_fields": True,
    "ignore_paths": [],
}

# Keys that used to live under `dvc:` but are no longer honored. Loader rejects
# them loudly so a stale `.ailine.yml` does not silently change behaviour.
REMOVED_DVC_KEYS = {
    "mode": "single allowed value, no longer configurable",
    "scope": "single allowed value, no longer configurable",
    "auto_pull_missing": "removed with materialize_dvc_linkage; restore feature will reintroduce",
    "status_verbose_limit": "never read by any code path",
}

DEFAULT_ENVIRONMENT_CONFIG = {
    "enabled": True,
    "packages": ["mlflow", "dvc"],
}

DEFAULT_RUN_CAPTURE_CONFIG = {
    "enabled": True,
}

# `project:` documents intent and pins schema version for migrations.
SUPPORTED_PROJECT_VERSIONS = {1}
DEFAULT_PROJECT_CONFIG = {
    "version": 1,
    "mode": "track",  # "track" -> pip-installable workflow, "demo" -> legacy `init/run`
}
VALID_PROJECT_MODES = {"track", "demo"}

# `track:` controls how `ailine track --` behaves around the user's argv.
DEFAULT_TRACK_CONFIG = {
    "repo_root": "auto",  # "auto" walks parents for .git; or absolute path
    "mlflow": {
        "mode": "inherit",   # inherit | wrap | none
        "set_env": False,    # if true, ailine sets MLFLOW_TRACKING_URI before child
        "inherit_name_sync": "auto",  # off | auto | force
    },
    "dvc": {
        "verify": "off",        # off | warn | strict
        "verify_commands": [],  # list of argv lists like [["dvc", "status", "--quiet"]]
    },
}
VALID_MLFLOW_MODES = {"inherit", "wrap", "none"}
VALID_MLFLOW_INHERIT_NAME_SYNC = {"off", "auto", "force"}
VALID_DVC_VERIFY_LEVELS = {"off", "warn", "strict"}


class CommitType(str, Enum):
    GIT = "git"
    SNAPSHOT = "snapshot"
