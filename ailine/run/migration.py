"""One-time migration of legacy root-level state artifacts under ``.ailine/``.

Older AIline versions wrote ``ailine_tree.db``, ``ailine.log`` and
``ailine_config.txt`` next to ``.ailine.yml`` in the project root. Newer
versions consolidate those under ``.ailine/`` (alongside
``.ailine/snapshots`` and the content-addressed object store).

The CLI invokes :func:`migrate_legacy_state_artifacts` once on every command
*before* opening any of these artifacts, so existing checkouts continue to
work without manual intervention. Best-effort by design: if a file cannot be
moved we keep using the legacy location for that single artifact and log a
warning instead of aborting the user's command.

Only ``ailine``-owned artifacts are relocated. User-owned paths (``mlruns/``,
``repo/``, ``.ailine.yml``, ``.ailineignore``) are not touched.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable, Optional, Tuple

from ailine.config import constants


def _move_one(legacy: str, target: str) -> Optional[str]:
    """Move ``legacy`` -> ``target`` if appropriate. Returns ``target`` on success.

    Returns ``None`` (and logs a warning) when both paths exist (we keep the
    new path and leave legacy untouched so users can spot the duplicate), or
    when the move itself fails.
    """
    if not os.path.exists(legacy):
        return None
    if os.path.exists(target):
        logging.warning(
            "AIline state artifact present at both legacy %s and %s; "
            "using %s. Remove %s manually once you have confirmed the "
            "new copy is correct.",
            legacy,
            target,
            target,
            legacy,
        )
        return None
    target_dir = os.path.dirname(target)
    if target_dir:
        try:
            os.makedirs(target_dir, exist_ok=True)
        except OSError as exc:
            logging.warning(
                "Could not create %s for migration of %s: %s",
                target_dir,
                legacy,
                exc,
            )
            return None
    try:
        os.replace(legacy, target)
    except OSError as exc:
        logging.warning(
            "Failed to migrate %s -> %s: %s. Continuing with legacy path.",
            legacy,
            target,
            exc,
        )
        return None
    logging.info("Migrated AIline state artifact %s -> %s", legacy, target)
    return target


def migrate_legacy_state_artifacts(
    mapping: Iterable[Tuple[str, str]] = constants.LEGACY_STATE_ARTIFACT_MAP,
) -> None:
    """Best-effort relocation of legacy root-level artifacts under ``.ailine/``.

    Safe to call repeatedly: moves only files that exist at the legacy path
    and not yet at the new path. Defaults to ``constants.LEGACY_STATE_ARTIFACT_MAP``;
    accepts an explicit mapping for tests.
    """
    for legacy, target in mapping:
        _move_one(legacy, target)
