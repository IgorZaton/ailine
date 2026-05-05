"""Snapshot storage directory resolution.

Single source of truth for where snapshot bundles + content-addressed objects
live. Resolution order:

1. ``AILINE_STORAGE_DIR`` environment variable (ops escape hatch).
2. ``snapshot.storage_dir`` from the validated ``.ailine.yml`` (relative
   values are resolved against ``repo_root``).
3. :data:`ailine.config.constants.DEFAULT_STORAGE_DIR`.

Returns an absolute path; never mutates inputs.
"""

from __future__ import annotations

import os
from typing import Any

from ailine.config import constants


def resolve_storage_dir(snapshot_cfg: Any, repo_root: str) -> str:
    """Resolve the snapshot storage directory.

    Parameters
    ----------
    snapshot_cfg:
        The ``snapshot`` section of a validated config (mapping). May be
        ``None`` (treated as empty) for callers without a config.
    repo_root:
        Absolute path to the git work-tree; used to resolve relative
        ``snapshot.storage_dir`` values.
    """
    env_override = os.environ.get("AILINE_STORAGE_DIR")
    if env_override:
        return os.path.abspath(env_override)

    cfg_value = None
    if isinstance(snapshot_cfg, dict):
        cfg_value = snapshot_cfg.get("storage_dir")
    if isinstance(cfg_value, str) and cfg_value.strip():
        candidate = cfg_value.strip()
        if not os.path.isabs(candidate):
            candidate = os.path.join(repo_root, candidate)
        return os.path.abspath(candidate)

    return os.path.abspath(constants.DEFAULT_STORAGE_DIR)
