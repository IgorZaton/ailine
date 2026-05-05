"""Plan/apply helpers for ``ailine remove`` and ``ailine purge``.

Two narrow responsibilities live here:

* **remove** — given a record id, compute the set of on-disk files
  (manifest/metadata/diff) and content-addressed objects that this row
  uniquely owns, then delete them and the lineage row. Object orphan
  detection diffs *this* row's manifest against the union of every other
  snapshot row's manifest so shared objects survive.

* **purge** — wipe AIline state and workspace config from a project:
  ``.ailine/`` (auto-generated bookkeeping + the default snapshot store) and
  the user-facing ``.ailine.yml`` / ``.ailineignore`` files. ``mlruns/`` and
  ``repo/`` are intentionally untouched (those are user-owned domains).

Both halves expose a ``plan_*`` function (pure, no side effects) and an
``apply_*`` function (mutating). The CLI layer prints the plan and prompts
when needed; the logic here stays straight-line and unit-testable.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field
from typing import List, Optional

from ailine.config import constants
from ailine.persistence import repository
from ailine.snapshot import object_store


@dataclass
class RemovePlan:
    """What ``ailine remove <id>`` would touch on disk and in the DB."""

    record_id: str
    record_name: Optional[str]
    record_type: str
    mlflow_run_id: Optional[str]
    artifact_files: List[str] = field(default_factory=list)
    orphan_object_paths: List[str] = field(default_factory=list)


@dataclass
class PurgePlan:
    """What ``ailine purge`` would remove from the project."""

    state_dir: Optional[str]
    extra_storage_dir: Optional[str]
    config_path: Optional[str]
    ailineignore_path: Optional[str]
    db_row_count: int

    def all_paths(self) -> List[str]:
        return [
            p
            for p in (
                self.state_dir,
                self.extra_storage_dir,
                self.config_path,
                self.ailineignore_path,
            )
            if p
        ]


def _read_manifest_shas(manifest_path: Optional[str]) -> set[str]:
    """Return the set of ``sha256`` keys referenced by ``manifest_path``.

    Missing/unreadable manifests yield an empty set: there is nothing safe to
    GC from a row whose manifest we cannot inspect.
    """
    if not manifest_path or not os.path.exists(manifest_path):
        return set()
    try:
        with open(manifest_path, "r", encoding="utf-8") as f:
            entries = json.load(f) or []
    except (OSError, ValueError):
        return set()
    shas: set[str] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        sha = entry.get("sha256")
        if isinstance(sha, str) and sha:
            shas.add(sha)
    return shas


def _existing_artifact_files(row: dict) -> List[str]:
    """Pick the per-snapshot files that exist on disk for one row."""
    candidates: List[str] = []
    for column in ("manifest_path", "metadata_path", "diff_path"):
        value = row.get(column)
        if value and os.path.exists(value):
            candidates.append(value)
    return candidates


def plan_remove_record(
    record_id: str,
    *,
    storage_dir: str,
    db_path: Optional[str] = None,
) -> RemovePlan:
    """Build the ``RemovePlan`` for ``record_id`` without touching anything.

    Raises ``LookupError`` when the row is missing. Callers (the CLI) wrap it
    in a fail-fast error message.
    """
    row = repository.fetch_record_for_remove(record_id, db_path=db_path)
    if not row:
        raise LookupError(record_id)

    artifact_files = _existing_artifact_files(row)
    target_shas = _read_manifest_shas(row.get("manifest_path"))

    other_shas: set[str] = set()
    if target_shas:
        other_rows = repository.fetch_all_snapshot_locations(db_path=db_path)
        for other in other_rows:
            if other["id"] == record_id:
                continue
            other_shas |= _read_manifest_shas(other.get("manifest_path"))

    orphan_shas = target_shas - other_shas
    orphan_object_paths = [
        object_store.object_path(sha, storage_dir) for sha in sorted(orphan_shas)
    ]
    orphan_object_paths = [p for p in orphan_object_paths if os.path.exists(p)]

    return RemovePlan(
        record_id=record_id,
        record_name=row.get("record_name"),
        record_type=row.get("type") or "",
        mlflow_run_id=row.get("mlflow_run"),
        artifact_files=artifact_files,
        orphan_object_paths=orphan_object_paths,
    )


def apply_remove_plan(
    plan: RemovePlan,
    *,
    with_mlflow: bool,
    db_path: Optional[str] = None,
    mlflow_client_factory=None,
) -> None:
    """Materialize ``plan`` against the filesystem and the lineage DB.

    Order: artifact files -> orphan objects -> DB row -> (optional) MLflow
    run delete. The MLflow step is best-effort: a failure is logged but does
    not undo the local cleanup.
    """
    for path in plan.artifact_files:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"Failed to remove {path}: {exc}") from exc

    for path in plan.orphan_object_paths:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise RuntimeError(f"Failed to remove object {path}: {exc}") from exc

    repository.delete_run(plan.record_id, db_path=db_path)

    if with_mlflow and plan.mlflow_run_id:
        try:
            if mlflow_client_factory is None:
                from mlflow.tracking import MlflowClient

                client = MlflowClient()
            else:
                client = mlflow_client_factory()
            client.delete_run(plan.mlflow_run_id)
        except Exception as exc:
            logging.warning(
                "ailine remove: MLflow delete_run(%s) failed: %s",
                plan.mlflow_run_id,
                exc,
            )


def _resolve_extra_storage_dir(
    snapshot_cfg: dict,
    state_dir_abs: str,
) -> Optional[str]:
    """Return the storage_dir to wipe **only** when it lives outside ``.ailine/``.

    The default storage path lives inside ``.ailine/`` and is removed via
    ``state_dir`` already. A user who pointed ``snapshot.storage_dir`` to
    ``./snapshots`` (for example) gets that directory wiped too.
    """
    cfg_value = None
    if isinstance(snapshot_cfg, dict):
        cfg_value = snapshot_cfg.get("storage_dir")
    if not isinstance(cfg_value, str) or not cfg_value.strip():
        return None
    candidate = cfg_value.strip()
    if not os.path.isabs(candidate):
        candidate = os.path.abspath(candidate)
    candidate = os.path.abspath(candidate)
    state_dir_abs = os.path.abspath(state_dir_abs)
    if candidate == state_dir_abs or candidate.startswith(state_dir_abs + os.sep):
        return None
    if not os.path.exists(candidate):
        return None
    return candidate


def plan_purge_workspace(
    repo_root: str,
    *,
    snapshot_cfg: Optional[dict] = None,
    db_path: Optional[str] = None,
) -> PurgePlan:
    """Build the ``PurgePlan`` for ``repo_root`` without touching the disk.

    The plan only lists paths that actually exist; the CLI prints them so
    users see the real fan-out before confirming.
    """
    repo_root_abs = os.path.abspath(repo_root)

    state_dir_raw = constants.STATE_DIR
    if not os.path.isabs(state_dir_raw):
        state_dir_abs = os.path.join(repo_root_abs, state_dir_raw)
    else:
        state_dir_abs = state_dir_raw
    state_dir = state_dir_abs if os.path.isdir(state_dir_abs) else None

    extra_storage_dir = _resolve_extra_storage_dir(snapshot_cfg or {}, state_dir_abs)

    config_path_raw = constants.POLICY_PATH
    if not os.path.isabs(config_path_raw):
        config_path_abs = os.path.join(repo_root_abs, config_path_raw)
    else:
        config_path_abs = config_path_raw
    config_path = config_path_abs if os.path.isfile(config_path_abs) else None

    from ailine.snapshot.ignore import AILINEIGNORE_FILENAME

    ailineignore_abs = os.path.join(repo_root_abs, AILINEIGNORE_FILENAME)
    ailineignore_path = (
        ailineignore_abs if os.path.isfile(ailineignore_abs) else None
    )

    db_row_count = repository.count_rows(db_path=db_path)

    return PurgePlan(
        state_dir=state_dir,
        extra_storage_dir=extra_storage_dir,
        config_path=config_path,
        ailineignore_path=ailineignore_path,
        db_row_count=db_row_count,
    )


def apply_purge_plan(plan: PurgePlan) -> None:
    """Delete every entry from ``plan``. Idempotent: missing paths are skipped."""
    for path in (plan.state_dir, plan.extra_storage_dir):
        if not path:
            continue
        if os.path.isdir(path):
            shutil.rmtree(path)
    for path in (plan.config_path, plan.ailineignore_path):
        if not path:
            continue
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
