"""Resolve a Git work-tree root from a starting directory.

Used by ``ailine track`` to find the repo of the user's current project so
they can ``cd`` anywhere inside the tree and still get a valid snapshot. Kept
out of :mod:`ailine.run.session` to honour DIP: the orchestrator receives an
already-resolved absolute path.
"""

from __future__ import annotations

import os
import subprocess
from typing import Optional


def discover_git_root(start: str) -> Optional[str]:
    """Walk parents of ``start`` looking for a ``.git`` entry.

    Returns the absolute path or ``None`` if no Git work-tree is found. We do
    not invoke ``git`` here so the helper stays usable when the CLI is missing
    (``ailine doctor`` reports that separately).
    """
    current = os.path.abspath(start)
    while True:
        if os.path.exists(os.path.join(current, ".git")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def resolve_git_root(start: str, configured: str = "auto") -> str:
    """Honour ``track.repo_root`` config: ``auto`` walks parents, otherwise treat as a path.

    Raises ``FileNotFoundError`` when no repo can be located so callers can
    translate that into a fail-fast CLI error.
    """
    if configured != "auto":
        candidate = os.path.abspath(configured)
        if not os.path.isdir(os.path.join(candidate, ".git")):
            raise FileNotFoundError(
                f"track.repo_root={configured!r} is not a Git work-tree."
            )
        return candidate

    found = discover_git_root(start)
    if not found:
        raise FileNotFoundError(
            f"No Git work-tree found at or above {start!r}. "
            "Run from inside a Git repo or set track.repo_root in .ailine.yml."
        )
    return found


def origin_url(repo_root: str) -> Optional[str]:
    """Best-effort lookup of the ``origin`` remote URL using ``git config``."""
    try:
        proc = subprocess.run(
            ["git", "-C", repo_root, "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return None
    if proc.returncode != 0:
        return None
    url = proc.stdout.strip()
    return url or None
