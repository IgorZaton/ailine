"""Minimal environment fingerprint: Python, platform, poetry.lock, pkg versions."""

import importlib.metadata
import os
import platform
import sys
from datetime import datetime
from typing import Tuple

from ailine.snapshot.paths import sha256_file


def collect_environment_fingerprint(repo_root: str, env_cfg: dict) -> Tuple[dict, str]:
    if not env_cfg.get("enabled", True):
        return {"enabled": False}, "missing"

    status = "complete"
    poetry_lock_path = os.path.join(repo_root, "poetry.lock")
    poetry_lock_sha256 = None
    if os.path.exists(poetry_lock_path):
        poetry_lock_sha256 = sha256_file(poetry_lock_path)
    else:
        status = "partial"

    package_versions = {}
    for package in env_cfg.get("packages", []):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = None
            status = "partial"

    fingerprint = {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "poetry_lock_sha256": poetry_lock_sha256,
        "packages": package_versions,
        "captured_at": datetime.now().isoformat(),
    }
    return fingerprint, status
