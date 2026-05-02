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
}

DEFAULT_DVC_CONFIG = {
    "mode": "local_or_remote",
    "scope": "all_dvc_tracked",
    "remote_name": None,
    "auto_pull_missing": True,
    "require_hash_fields": True,
    "status_verbose_limit": 10,
    "ignore_paths": [],
}
VALID_DVC_MODES = {"local_or_remote"}
VALID_DVC_SCOPES = {"all_dvc_tracked"}

DEFAULT_ENVIRONMENT_CONFIG = {
    "enabled": True,
    "packages": ["mlflow", "flask", "gitpython", "dvc"],
}

DEFAULT_RUN_CAPTURE_CONFIG = {
    "enabled": True,
}


class CommitType(str, Enum):
    GIT = "git"
    SNAPSHOT = "snapshot"
