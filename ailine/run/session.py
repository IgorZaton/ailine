"""Run-tracking session: orchestrates one experiment recording.

This module is the **single shared core** for both ``ailine track --`` and the
demo ``ailine run`` commands. It does NOT call ``click`` directly so it can be
unit-tested as plain Python; CLI entry points are responsible for argument
parsing and exit code propagation.

Flow:

1. Resolve git state (HEAD sha, dirty?) at ``git_root``.
2. If dirty: scan, resolve large-file decisions, build manifest, create
   snapshot bundle (tar.zst + manifest + diff + metadata).
3. Optionally run ``track.dvc.verify_commands`` (controlled by ``track.dvc.verify``).
4. Build DVC linkage, environment fingerprint, run-command payload from the
   real argv.
5. Run the user's command via ``subprocess`` from ``git_root``, optionally
   wrapped in ``mlflow.start_run`` per ``track.mlflow.mode``.
6. Insert :class:`RunRecord` into the SQLite tree.

Only the child's exit code is propagated to the caller; AIline's own failures
raise :class:`SessionError` (callers translate to ``click.ClickException``).
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

import git
import mlflow

from ailine.config import constants
from ailine.config.defaults import CommitType
from ailine.config.validate import ValidatedConfig
from ailine.fingerprint.env import collect_environment_fingerprint
from ailine.linkage.dvc import build_dvc_linkage
from ailine.persistence import repository
from ailine.persistence.repository import RunRecord
from ailine.snapshot.archive import create_snapshot
from ailine.snapshot.manifest import build_manifest
from ailine.snapshot.paths import normalize_rel_path
from ailine.snapshot.scan import resolve_large_file_decisions, scan_repo_files


class SessionError(Exception):
    """AIline-side failure during a tracked run (config, snapshot, DB)."""


def _guard_child_argv0(git_root: str, argv: Sequence[str]) -> None:
    """Fail fast when argv[0] looks like a script path but is not executable.

    ``subprocess`` uses argv[0] as the program image; a non-executable ``.py``
    file raises ``PermissionError`` with a cryptic errno. Users often write
    ``ailine track -- repo/train.py`` meaning ``python repo/train.py``.
    """
    prog = argv[0]
    if shutil.which(prog):
        return
    candidate = prog if os.path.isabs(prog) else os.path.normpath(os.path.join(git_root, prog))
    if not os.path.isfile(candidate):
        return
    if os.access(candidate, os.X_OK):
        return
    remainder = " ".join(shlex.quote(a) for a in argv[1:])
    tail = f" {remainder}" if remainder else ""
    if candidate.endswith(".py"):
        raise SessionError(
            f"Cannot execute {prog!r} as a program: it is a Python file without execute bit. "
            f"Use an interpreter, e.g.  ailine track -- python {shlex.quote(prog)}{tail}"
        )
    extra = f" Remaining arguments:{tail}" if tail else ""
    raise SessionError(
        f"Cannot execute {prog!r}: file exists under the repo but is not executable. "
        f"Run it via the correct interpreter, or chmod +x if it has a shebang.{extra}"
    )


@dataclass
class TrackResult:
    """Summary returned to CLI callers."""

    exit_code: int
    commit_id: str
    commit_type: str
    record: Optional[RunRecord] = None
    snapshot_path: Optional[str] = None
    mlflow_run_id: Optional[str] = None


def _maybe_origin_url(repo: git.Repo) -> Optional[str]:
    try:
        return repo.remotes.origin.url
    except (AttributeError, ValueError):
        return None


def _run_dvc_verify(
    git_root: str, verify_commands: Sequence[Sequence[str]], verify_level: str
) -> List[dict]:
    """Run optional `dvc status` style verification commands.

    Returns a list of {cmd, returncode} dicts. Raises SessionError when
    ``verify_level == "strict"`` and any command returns non-zero.
    """
    results: List[dict] = []
    for cmd in verify_commands:
        proc = subprocess.run(
            list(cmd), cwd=git_root, check=False, capture_output=True, text=True
        )
        results.append({"cmd": list(cmd), "returncode": proc.returncode})
        if proc.returncode != 0:
            msg = f"track.dvc.verify_commands failed: {' '.join(cmd)} (exit {proc.returncode})"
            if verify_level == "strict":
                raise SessionError(msg)
            if verify_level == "warn":
                logging.warning(msg)
    return results


def _snapshot_if_dirty(
    repo: git.Repo, git_root: str, storage: str, snapshot_policy: dict
) -> dict:
    """Return a dict with keys present iff a snapshot was created."""
    if not repo.is_dirty(untracked_files=True):
        return {}

    entries = scan_repo_files(git_root, snapshot_policy)
    entries, _store = resolve_large_file_decisions(entries, snapshot_policy)
    manifest_entries, archive_entries, manifest_extra = build_manifest(entries, storage)
    diff_text = repo.git.diff("HEAD")
    untracked_files = [normalize_rel_path(p) for p in repo.untracked_files]

    snapshot_result = create_snapshot(
        manifest_entries=manifest_entries,
        archive_entries=archive_entries,
        parent_commit_hash=repo.head.commit.hexsha,
        storage_dir=storage,
        diff_text=diff_text,
        untracked_files=untracked_files,
        repo_path=git_root,
        write_meta_file=False,
    )
    summary = manifest_extra["summary"]
    snapshot_result["included_file_count"] = summary["included_file_count"]
    snapshot_result["excluded_file_count"] = summary["excluded_file_count"]
    snapshot_result["large_file_pointer_count"] = summary["large_file_pointer_count"]
    snapshot_result["scanned_count"] = len(entries)
    snapshot_result["included_bytes"] = summary["included_bytes"]
    return snapshot_result


def _maybe_set_mlflow_env(track_mlflow_cfg: dict) -> Optional[dict]:
    """When ``set_env=true``, inject MLFLOW_TRACKING_URI into the child env.

    Returns an env dict (copy of os.environ with overrides) or None when no
    overrides are needed.
    """
    if not track_mlflow_cfg.get("set_env"):
        return None
    env = os.environ.copy()
    if "MLFLOW_TRACKING_URI" not in env:
        env["MLFLOW_TRACKING_URI"] = mlflow.get_tracking_uri()
    return env


def _execute_child(
    argv: Sequence[str], cwd: str, env: Optional[dict], mlflow_mode: str, run_name: str
) -> tuple[int, Optional[str]]:
    """Run the user command. Returns (exit_code, mlflow_run_id_or_None)."""
    if mlflow_mode == "wrap":
        with mlflow.start_run(run_name=run_name):
            run_id = mlflow.active_run().info.run_id
            proc = subprocess.run(list(argv), cwd=cwd, env=env, check=False)
            return proc.returncode, run_id
    proc = subprocess.run(list(argv), cwd=cwd, env=env, check=False)
    return proc.returncode, None


def _start_time_utc(val) -> Optional[datetime]:
    """Normalize ``mlflow.search_runs`` ``start_time`` cell to aware UTC."""
    if val is None:
        return None
    try:
        import pandas as pd

        if pd.isna(val):
            return None
    except Exception:
        pass
    if hasattr(val, "to_pydatetime"):
        dt = val.to_pydatetime()
    elif isinstance(val, datetime):
        dt = val
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _best_effort_mlflow_run_after_inherit_child(
    since_utc: datetime, until_utc: datetime
) -> Optional[str]:
    """Newest MLflow run whose ``start_time`` falls between subprocess bounds (slack).

    Used for ``track.mlflow.mode == inherit`` when the training script opens its
    own ``mlflow.start_run`` so the commits table can show a run id.
    """
    slack = timedelta(seconds=30)
    window_lo = since_utc - slack
    window_hi = until_utc + slack
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        df = mlflow.search_runs(order_by=["start_time DESC"], max_results=64)
    except Exception as exc:
        logging.debug("MLflow search_runs after inherit child failed: %s", exc)
        return None
    if df is None or getattr(df, "empty", True):
        return None
    if "run_id" not in df.columns or "start_time" not in df.columns:
        return None
    for _, row in df.iterrows():
        st = _start_time_utc(row["start_time"])
        if st is None:
            continue
        if window_lo <= st <= window_hi:
            rid = row.get("run_id")
            return str(rid) if rid else None
    return None


def run_tracked_command(
    *,
    git_root: str,
    argv: Sequence[str],
    storage: str,
    config: ValidatedConfig,
    git_url_hint: Optional[str] = None,
    run_name: Optional[str] = None,
) -> TrackResult:
    """Execute one tracked run and persist a :class:`RunRecord`.

    Parameters
    ----------
    git_root: absolute path to the repository work-tree.
    argv: child command argv; runs as-is via ``subprocess`` (NOT a shell).
    storage: snapshot storage directory.
    config: validated ``.ailine.yml`` bundle.
    git_url_hint: optional pre-resolved remote URL (e.g. from `init` config).
    run_name: optional MLflow run name when ``track.mlflow.mode == 'wrap'``.
    """
    if not argv:
        raise SessionError("argv must contain at least one element")
    if not os.path.isdir(git_root):
        raise SessionError(f"git_root does not exist: {git_root}")
    _guard_child_argv0(git_root, argv)

    repo = git.Repo(git_root)
    head_sha = repo.head.commit.hexsha

    snap = _snapshot_if_dirty(repo, git_root, storage, config.snapshot)
    if snap:
        commit_id = snap["snapshot_hash"]
        commit_type = CommitType.SNAPSHOT
        parent = head_sha[:7]
    else:
        commit_id = head_sha
        commit_type = CommitType.GIT
        parent = None

    track_dvc = config.track["dvc"]
    if track_dvc["verify_commands"]:
        _run_dvc_verify(git_root, track_dvc["verify_commands"], track_dvc["verify"])

    dvc_linkage = build_dvc_linkage(git_root, config.dvc)
    env_fingerprint, env_fingerprint_status = collect_environment_fingerprint(
        git_root, config.environment
    )

    captured_at = datetime.now().isoformat()
    run_command_summary = " ".join(argv)
    if config.run_capture.get("enabled", True):
        run_command_payload = {
            "argv": list(argv),
            "cwd": git_root,
            "captured_at": captured_at,
            "resolved_command": run_command_summary,
        }
    else:
        run_command_payload = {}
        run_command_summary = None

    track_mlflow = config.track["mlflow"]
    child_env = _maybe_set_mlflow_env(track_mlflow)
    effective_run_name = run_name or f"exp_{commit_id[:8]}"
    since_utc = datetime.now(timezone.utc)
    exit_code, mlflow_run_id = _execute_child(
        argv=argv,
        cwd=git_root,
        env=child_env,
        mlflow_mode=track_mlflow["mode"],
        run_name=effective_run_name,
    )
    until_utc = datetime.now(timezone.utc)
    if track_mlflow["mode"] == "inherit" and not mlflow_run_id:
        mlflow_run_id = _best_effort_mlflow_run_after_inherit_child(since_utc, until_utc)

    git_url = git_url_hint or _maybe_origin_url(repo)

    record = RunRecord(
        id=commit_id,
        type=commit_type.value,
        parent=parent,
        mlflow_run=mlflow_run_id,
        dvc_version=None,
        snapshot_path=snap.get("snapshot_path") if snap else None,
        timestamp=datetime.now().isoformat(),
        git_url=git_url if commit_type == CommitType.GIT else None,
        manifest_path=snap.get("manifest_path") if snap else None,
        metadata_path=snap.get("metadata_path") if snap else None,
        archive_bytes=snap.get("archive_bytes") if snap else None,
        included_file_count=snap.get("included_file_count") if snap else None,
        excluded_file_count=snap.get("excluded_file_count") if snap else None,
        large_file_pointer_count=snap.get("large_file_pointer_count") if snap else None,
        diff_path=snap.get("diff_path") if snap else None,
        dvc_linkage_json=json.dumps(dvc_linkage, sort_keys=True),
        dvc_linkage_status=dvc_linkage["status"],
        env_fingerprint_json=json.dumps(env_fingerprint, sort_keys=True),
        env_fingerprint_status=env_fingerprint_status,
        run_command_json=(
            json.dumps(run_command_payload, sort_keys=True) if run_command_payload else None
        ),
        run_command_summary=run_command_summary,
    )
    repository.insert_run(record)

    return TrackResult(
        exit_code=exit_code,
        commit_id=commit_id,
        commit_type=commit_type.value,
        record=record,
        snapshot_path=record.snapshot_path,
        mlflow_run_id=mlflow_run_id,
    )
