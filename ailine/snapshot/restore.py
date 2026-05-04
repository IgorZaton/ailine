"""Strict-sync restore engine for ``objects-v1`` snapshots.

Pure execution: given a manifest, a storage directory, and a target repo
root, this module computes the write/delete plan and (optionally) applies
it. CLI concerns (flag parsing, user output) live in
[ailine.cli.restore](ailine/cli/restore.py).

Strict-sync semantics:

* Every manifest entry with ``classification == "include"`` and
  ``decision == "include"`` is materialized from the content-addressed
  object store into the worktree at its relative path.
* Files currently present in the worktree but not in that include set are
  removed, except those under ``PROTECTED_DIR_NAMES`` (e.g. ``.git``,
  ``.ailine``) and inside ``storage_dir`` when nested in the repo.
* Manifest entries with non-include classification (DVC pointers, large
  non-DVC, excluded-by-policy) are reported back to the caller; the engine
  itself never attempts to materialize or delete them.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import List

import pathspec

from ailine.snapshot import object_store
from ailine.snapshot.ignore import is_ignored


PROTECTED_DIR_NAMES = (".git", ".ailine")


@dataclass(frozen=True)
class RestoreEntry:
    rel_path: str
    sha256: str


@dataclass
class RestorePlan:
    writes: List[RestoreEntry] = field(default_factory=list)
    deletions: List[str] = field(default_factory=list)
    skipped_pointer_paths: List[str] = field(default_factory=list)
    missing_objects: List[RestoreEntry] = field(default_factory=list)


def _is_safe_relative(rel_path: str) -> bool:
    """Reject empty, absolute, traversal, or NUL-bearing paths."""
    if not rel_path or "\x00" in rel_path:
        return False
    if os.path.isabs(rel_path):
        return False
    norm = os.path.normpath(rel_path).replace(os.sep, "/")
    if norm in {"", "."} or norm.startswith("../") or norm == "..":
        return False
    parts = norm.split("/")
    if any(part == ".." for part in parts):
        return False
    return True


def _under_protected(rel_path: str) -> bool:
    parts = rel_path.replace(os.sep, "/").split("/")
    return bool(parts) and parts[0] in PROTECTED_DIR_NAMES


def collect_restore_entries(manifest_entries: list) -> tuple[list[RestoreEntry], list[str]]:
    """Split manifest entries into restorable and skipped-pointer paths.

    Raises ``ValueError`` when a manifest entry carries an unsafe path. The
    caller surfaces that as a fail-fast preflight error.
    """
    restore: list[RestoreEntry] = []
    skipped_pointers: list[str] = []
    for entry in manifest_entries:
        rel = entry.get("path") or ""
        classification = entry.get("classification")
        decision = entry.get("decision")
        if not _is_safe_relative(rel):
            raise ValueError(f"unsafe path in manifest: {rel!r}")
        if classification == "include" and decision == "include":
            sha = entry.get("sha256")
            if not sha:
                raise ValueError(f"manifest entry missing sha256 for {rel!r}")
            restore.append(RestoreEntry(rel_path=rel, sha256=sha))
            continue
        if classification in {"large-and-dvc", "large-non-dvc"}:
            skipped_pointers.append(rel)
    return restore, skipped_pointers


def _walk_repo_files(repo_root: str, storage_dir: str) -> list[str]:
    """List repo-relative file paths, skipping protected and storage dirs."""
    repo_root_abs = os.path.abspath(repo_root)
    storage_abs = os.path.abspath(storage_dir) if storage_dir else None
    out: list[str] = []
    for root, dirs, files in os.walk(repo_root_abs):
        dirs[:] = [d for d in dirs if d not in PROTECTED_DIR_NAMES]
        if storage_abs:
            dirs[:] = [
                d
                for d in dirs
                if os.path.abspath(os.path.join(root, d)) != storage_abs
            ]
        for filename in files:
            full = os.path.abspath(os.path.join(root, filename))
            try:
                rel = os.path.relpath(full, repo_root_abs).replace(os.sep, "/")
            except ValueError:
                continue
            if _under_protected(rel):
                continue
            out.append(rel)
    return out


def plan_restore(
    manifest_entries: list,
    storage_dir: str,
    repo_root: str,
    preserve_spec: pathspec.PathSpec | None = None,
) -> RestorePlan:
    """Compute the strict-sync restore plan for a snapshot manifest.

    Pre-verifies that every required object exists in ``storage_dir`` so
    the apply phase only runs when the bundle is complete.
    """
    plan = RestorePlan()
    restore, skipped_pointers = collect_restore_entries(manifest_entries)
    plan.skipped_pointer_paths = skipped_pointers

    restore_paths = {e.rel_path for e in restore}

    for entry in restore:
        if not object_store.has_object(entry.sha256, storage_dir):
            plan.missing_objects.append(entry)
        else:
            plan.writes.append(entry)

    current_files = _walk_repo_files(repo_root, storage_dir)
    if preserve_spec is None:
        plan.deletions = sorted(p for p in current_files if p not in restore_paths)
    else:
        plan.deletions = sorted(
            p
            for p in current_files
            if p not in restore_paths and not is_ignored(p, preserve_spec)
        )
    plan.writes.sort(key=lambda e: e.rel_path)
    return plan


def _atomic_write(target_path: str, payload: bytes) -> None:
    target_dir = os.path.dirname(target_path)
    os.makedirs(target_dir, exist_ok=True)
    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".restore.", dir=target_dir)
    os.close(tmp_fd)
    try:
        with open(tmp_path, "wb") as f:
            f.write(payload)
        os.replace(tmp_path, target_path)
    except BaseException:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise


def apply_restore(
    plan: RestorePlan,
    storage_dir: str,
    repo_root: str,
) -> None:
    """Execute ``plan`` against the worktree.

    Writes are atomic per file (tmp + ``os.replace``); deletions follow only
    after writes succeed so a partial failure leaves a superset of the
    target state, never a strict subset.
    """
    if plan.missing_objects:
        raise RuntimeError(
            f"refusing to apply: {len(plan.missing_objects)} object(s) missing from storage"
        )

    repo_root_abs = os.path.abspath(repo_root)
    for entry in plan.writes:
        target = os.path.abspath(os.path.join(repo_root_abs, entry.rel_path))
        if os.path.commonpath([target, repo_root_abs]) != repo_root_abs:
            raise RuntimeError(f"refusing to write outside repo: {entry.rel_path}")
        payload = object_store.read_object_bytes(entry.sha256, storage_dir)
        _atomic_write(target, payload)

    for rel in plan.deletions:
        target = os.path.abspath(os.path.join(repo_root_abs, rel))
        if os.path.commonpath([target, repo_root_abs]) != repo_root_abs:
            continue
        if os.path.exists(target):
            try:
                os.remove(target)
            except OSError:
                pass
            _prune_empty_dirs(os.path.dirname(target), repo_root_abs)


def _prune_empty_dirs(start_dir: str, repo_root_abs: str) -> None:
    """Remove directories left empty by deletions, stopping at ``repo_root``."""
    current = os.path.abspath(start_dir)
    while current.startswith(repo_root_abs) and current != repo_root_abs:
        try:
            if not os.listdir(current):
                os.rmdir(current)
            else:
                return
        except OSError:
            return
        current = os.path.dirname(current)
