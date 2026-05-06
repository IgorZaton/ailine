"""Run-tracking session: orchestrates one experiment recording.

This module is the **single shared core** for both ``ailine track --`` and the
demo ``ailine run`` commands. It does NOT call ``click`` directly so it can be
unit-tested as plain Python; CLI entry points are responsible for argument
parsing and exit code propagation.

Flow:

1. Resolve git state (HEAD sha, dirty?) at ``git_root``.
2. If dirty: scan, resolve large-file decisions, build manifest, create
   snapshot bundle (manifest + diff + metadata + content-addressed objects).
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

import contextlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterator, List, Optional, Sequence

import git
import mlflow
from mlflow.tracking import MlflowClient

from ailine.config import constants
from ailine.integrations.mlflow_plugin import CORRELATION_ENV, CORRELATION_TAG
from ailine.config.defaults import CommitType
from ailine.config.validate import ValidatedConfig
from ailine.fingerprint.env import collect_environment_fingerprint
from ailine.linkage.dvc import build_dvc_linkage
from ailine.naming.petname import default_record_name, validate_record_name
from ailine.persistence import repository
from ailine.persistence.repository import RunRecord
from ailine.snapshot.archive import create_snapshot
from ailine.snapshot.manifest import build_manifest
from ailine.snapshot.paths import normalize_rel_path
from ailine.snapshot.scan import resolve_large_file_decisions, scan_repo_files

_AUTO_MLFLOW_NAME_RE = re.compile(r"^[a-z0-9]+-[a-z0-9]+(?:-[a-z0-9]+)?$")


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
    record_name: Optional[str] = None


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


def _maybe_set_mlflow_env(
    track_mlflow_cfg: dict,
    prelink_run_id: Optional[str] = None,
    correlation_id: Optional[str] = None,
) -> Optional[dict]:
    """Build the child env dict with optional MLflow + AIline overrides.

    When ``set_env=true`` injects ``MLFLOW_TRACKING_URI``. When
    ``prelink_run_id`` is provided (legacy inherit-mode prelink path), exports
    ``MLFLOW_RUN_ID`` so a plain ``mlflow.start_run()`` in the user's script
    resumes the AIline-pre-created run instead of creating a fresh one.
    When ``correlation_id`` is provided (tag-based linking), exports
    ``AILINE_CORRELATION_ID`` so the AIline MLflow plugin can tag every run
    started inside that child process. Never overwrites a user-supplied
    value of any of these variables.

    Returns an env dict (copy of ``os.environ`` with overrides) or ``None``
    when no overrides are needed.
    """
    set_env = bool(track_mlflow_cfg.get("set_env"))
    has_prelink = bool(prelink_run_id)
    has_correlation = bool(correlation_id)
    if not set_env and not has_prelink and not has_correlation:
        return None
    env = os.environ.copy()
    if set_env and "MLFLOW_TRACKING_URI" not in env:
        env["MLFLOW_TRACKING_URI"] = mlflow.get_tracking_uri()
    if has_prelink and "MLFLOW_RUN_ID" not in env:
        env["MLFLOW_RUN_ID"] = str(prelink_run_id)
    if has_correlation and CORRELATION_ENV not in env:
        env[CORRELATION_ENV] = str(correlation_id)
    return env


def _precreate_mlflow_run_for_inherit(run_name: str) -> Optional[str]:
    """Best-effort pre-create an MLflow run for inherit mode.

    Returns the new ``run_id`` so AIline can populate the lineage row and
    export ``MLFLOW_RUN_ID`` to the child. Returns ``None`` if pre-creation
    fails for any reason (we degrade to today's post-hoc lookup rather than
    aborting the user's command).

    Experiment is resolved from ``MLFLOW_EXPERIMENT_ID`` /
    ``MLFLOW_EXPERIMENT_NAME`` if set, otherwise MLflow's ``Default``
    experiment (id ``"0"``). The run carries the ``mlflow.runName`` tag so
    list views in the MLflow UI stay readable.
    """
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        client = MlflowClient()
        experiment_id = os.environ.get("MLFLOW_EXPERIMENT_ID")
        if not experiment_id:
            name = os.environ.get("MLFLOW_EXPERIMENT_NAME")
            if name:
                experiment = client.get_experiment_by_name(name)
                experiment_id = (
                    experiment.experiment_id if experiment is not None else None
                )
                if experiment_id is None:
                    experiment_id = client.create_experiment(name)
        if not experiment_id:
            experiment_id = "0"
        run = client.create_run(
            experiment_id=str(experiment_id),
            tags={"mlflow.runName": run_name},
        )
        return run.info.run_id
    except Exception as exc:
        logging.debug("MLflow inherit-mode pre-link create_run failed: %s", exc)
        return None


def _resolve_run_by_correlation(correlation_id: str) -> Optional[str]:
    """One-shot lookup: newest MLflow run carrying the AIline correlation tag.

    Returns ``None`` on any error (server unreachable, malformed response,
    no match yet). The poller treats that uniformly as "not yet linked".
    """
    if not correlation_id:
        return None
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        client = MlflowClient()
        # Search across all active experiments. The correlation id is unique
        # per AIline track invocation, so the first match is authoritative
        # regardless of which experiment the user's code chose.
        experiment_ids = [exp.experiment_id for exp in client.search_experiments()]
        if not experiment_ids:
            return None
        runs = client.search_runs(
            experiment_ids=experiment_ids,
            filter_string=f'tags."{CORRELATION_TAG}" = "{correlation_id}"',
            max_results=1,
        )
        if not runs:
            return None
        return runs[0].info.run_id
    except Exception as exc:
        logging.debug("Correlation-tag MLflow lookup failed: %s", exc)
        return None


def _run_correlation_poller(
    correlation_id: str,
    record_id: str,
    poll_seconds: float,
    stop_event: threading.Event,
    result_holder: List[Optional[str]],
) -> None:
    """Background poller: link the lineage row mid-flight via correlation tag.

    Runs in a daemon thread for the duration of the user's subprocess. Exits
    immediately after the first successful update, or when ``stop_event`` is
    set after the child finishes (the caller does one final retry on exit).
    On success, appends the run id to ``result_holder`` so the caller can
    refresh its local mlflow_run_id without re-querying the DB.
    """
    while not stop_event.is_set():
        run_id = _resolve_run_by_correlation(correlation_id)
        if run_id and repository.set_mlflow_run(record_id, run_id):
            result_holder.append(run_id)
            return
        if stop_event.wait(timeout=poll_seconds):
            return


@contextlib.contextmanager
def _maybe_wrap_mlflow_run(mlflow_mode: str, run_name: str) -> Iterator[Optional[str]]:
    """Yield ``mlflow_run_id`` (or ``None`` for non-wrap modes).

    Splitting the wrap-mode context out of the subprocess call lets the caller
    insert an ``in_progress`` lifecycle row *before* the child starts, with the
    MLflow run id already known so the UI/CLI can surface a live link from the
    very first moment the run exists.
    """
    if mlflow_mode == "wrap":
        with mlflow.start_run(run_name=run_name):
            yield mlflow.active_run().info.run_id
    else:
        yield None


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


def _resolve_tracked_labels(record_name: Optional[str], run_name: Optional[str]) -> tuple[str, str]:
    """Return ``(record_label, mlflow_wrap_run_name)``.

    For traceability, the lineage DB name and MLflow ``run_name`` (wrap mode)
    use the same string unless the user sets both ``--name`` and ``--run-name``
    to different values.

    * Neither flag: random ``adjective-animal`` for both.
    * ``--name`` only: validated label for both.
    * ``--run-name`` only: same string for both (validated for DB storage).
    * Both: DB uses ``--name``; MLflow wrap uses ``--run-name`` verbatim.
    """
    rec_in = (record_name or "").strip()
    run_in = (run_name or "").strip()

    try:
        if rec_in and run_in:
            return validate_record_name(rec_in), run_in
        if rec_in:
            label = validate_record_name(rec_in)
            return label, label
        if run_in:
            label = validate_record_name(run_in)
            return label, label
        label = default_record_name()
        return label, label
    except ValueError as exc:
        raise SessionError(str(exc)) from exc


def _is_probably_auto_mlflow_name(current_name: Optional[str], run_id: str) -> bool:
    """Heuristic for conservative inherit-mode sync."""
    if current_name is None:
        return True
    name = str(current_name).strip()
    if not name:
        return True
    if name == run_id:
        return True
    if name.lower().startswith("run-") and run_id.startswith(name[4:]):
        return True
    return bool(_AUTO_MLFLOW_NAME_RE.fullmatch(name))


def _maybe_sync_inherit_mlflow_name(run_id: Optional[str], target_name: str, policy: str) -> None:
    """Best-effort post-hoc run-name alignment for ``track.mlflow.mode=inherit``."""
    if not run_id or not str(run_id).strip():
        return
    if policy == "off":
        return
    rid = str(run_id).strip()
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        client = MlflowClient()
        run = client.get_run(rid)
        tags = getattr(run.data, "tags", None) or {}
        current_name = tags.get("mlflow.runName")
        should_sync = policy == "force" or _is_probably_auto_mlflow_name(current_name, rid)
        if should_sync and current_name != target_name:
            client.set_tag(rid, "mlflow.runName", target_name)
    except Exception as exc:
        logging.debug("Skipping inherit MLflow name sync for %s: %s", rid, exc)


def run_tracked_command(
    *,
    git_root: str,
    argv: Sequence[str],
    storage: str,
    config: ValidatedConfig,
    git_url_hint: Optional[str] = None,
    run_name: Optional[str] = None,
    record_name: Optional[str] = None,
    on_resolved_labels: Optional[Callable[[str, str], None]] = None,
    on_run_started: Optional[Callable[[str, Optional[str]], None]] = None,
) -> TrackResult:
    """Execute one tracked run and persist a :class:`RunRecord`.

    Parameters
    ----------
    git_root: absolute path to the repository work-tree.
    argv: child command argv; runs as-is via ``subprocess`` (NOT a shell).
    storage: snapshot storage directory.
    config: validated ``.ailine.yml`` bundle.
    git_url_hint: optional pre-resolved remote URL (e.g. from `init` config).
    run_name: optional MLflow ``run_name`` when ``track.mlflow.mode == 'wrap'``.
        If omitted, uses the same string as ``record_name`` (see ``record_name``).
    record_name: optional lineage DB label; default is random ``adjective-animal``.
        When ``run_name`` is omitted, MLflow wrap mode uses this label too.
        If both are set, ``record_name`` is stored in the DB and ``run_name`` is
        passed to MLflow only.
    on_resolved_labels: optional callback ``(record_label, mlflow_wrap_name)``
        invoked once immediately after names are resolved (before snapshots /
        subprocess). Used by the CLI to print a preview line.
    on_run_started: optional callback ``(record_id, mlflow_run_id_or_None)``
        invoked once after the lifecycle row is inserted with ``status =
        in_progress`` and (for ``wrap`` mode) the MLflow run id is known.
        Fires before the child subprocess executes so the CLI/UI can surface a
        live link.
    """
    if not argv:
        raise SessionError("argv must contain at least one element")
    if not os.path.isdir(git_root):
        raise SessionError(f"git_root does not exist: {git_root}")
    _guard_child_argv0(git_root, argv)

    record_label, mlflow_wrap_name = _resolve_tracked_labels(record_name, run_name)
    if on_resolved_labels is not None:
        on_resolved_labels(record_label, mlflow_wrap_name)

    repo = git.Repo(git_root)
    head_sha = repo.head.commit.hexsha

    snap = _snapshot_if_dirty(repo, git_root, storage, config.snapshot)
    if snap:
        commit_id = snap["snapshot_hash"]
        commit_type = CommitType.SNAPSHOT
        # Full parent SHA for unambiguous git worktree / restore (UI may shorten for display).
        parent = head_sha
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
    link_strategy = str(track_mlflow.get("link_strategy", "tag"))

    # Inherit-mode link strategies (only meaningful when mode == "inherit"):
    #   tag     - inject AILINE_CORRELATION_ID and rely on the MLflow plugin
    #             + background poller to fill mlflow_run mid-flight.
    #   prelink - AIline pre-creates the MLflow run and exports MLFLOW_RUN_ID.
    #   none    - skip live linking entirely.
    # Wrap mode opens its own outer run via _maybe_wrap_mlflow_run, and
    # mlflow.mode == "none" skips MLflow entirely; both ignore link_strategy.
    prelink_run_id: Optional[str] = None
    correlation_id: Optional[str] = None
    if track_mlflow["mode"] == "inherit":
        if link_strategy == "prelink":
            prelink_run_id = _precreate_mlflow_run_for_inherit(record_label)
        elif link_strategy == "tag":
            correlation_id = uuid.uuid4().hex

    child_env = _maybe_set_mlflow_env(
        track_mlflow,
        prelink_run_id=prelink_run_id,
        correlation_id=correlation_id,
    )

    git_url = git_url_hint or _maybe_origin_url(repo)
    env_fingerprint_json = json.dumps(env_fingerprint, sort_keys=True)
    run_command_json = (
        json.dumps(run_command_payload, sort_keys=True) if run_command_payload else None
    )

    started_at = datetime.now().isoformat()
    since_utc = datetime.now(timezone.utc)

    # The lifecycle row is inserted from inside the optional MLflow wrap
    # context so the row reflects the MLflow run id (if any) the moment it
    # exists, and any AIline-side error after this point can be reliably
    # finalized via fail_run().
    exit_code: Optional[int] = None
    mlflow_run_id: Optional[str] = prelink_run_id
    record_inserted = False
    record: Optional[RunRecord] = None
    poller_thread: Optional[threading.Thread] = None
    poller_stop = threading.Event()
    poller_result: List[Optional[str]] = []

    try:
        with _maybe_wrap_mlflow_run(track_mlflow["mode"], mlflow_wrap_name) as wrap_run_id:
            if wrap_run_id is not None:
                mlflow_run_id = wrap_run_id

            record = RunRecord(
                id=commit_id,
                type=commit_type.value,
                parent=parent,
                mlflow_run=mlflow_run_id,
                dvc_version=None,
                snapshot_path=snap.get("snapshot_path") if snap else None,
                timestamp=started_at,
                git_url=git_url,
                manifest_path=snap.get("manifest_path") if snap else None,
                metadata_path=snap.get("metadata_path") if snap else None,
                archive_bytes=snap.get("archive_bytes") if snap else None,
                included_file_count=snap.get("included_file_count") if snap else None,
                excluded_file_count=snap.get("excluded_file_count") if snap else None,
                large_file_pointer_count=snap.get("large_file_pointer_count") if snap else None,
                diff_path=snap.get("diff_path") if snap else None,
                dvc_linkage_json=json.dumps(dvc_linkage, sort_keys=True),
                dvc_linkage_status=dvc_linkage["status"],
                env_fingerprint_json=env_fingerprint_json,
                env_fingerprint_status=env_fingerprint_status,
                run_command_json=run_command_json,
                run_command_summary=run_command_summary,
                record_name=record_label,
                started_at=started_at,
            )
            repository.insert_running_run(record)
            record_inserted = True

            if on_run_started is not None:
                on_run_started(commit_id, mlflow_run_id)

            if correlation_id is not None:
                poll_seconds = float(track_mlflow.get("link_poll_seconds", 3.0) or 3.0)
                poller_thread = threading.Thread(
                    target=_run_correlation_poller,
                    args=(
                        correlation_id,
                        commit_id,
                        poll_seconds,
                        poller_stop,
                        poller_result,
                    ),
                    daemon=True,
                    name="ailine-mlflow-link-poller",
                )
                poller_thread.start()

            proc = subprocess.run(list(argv), cwd=git_root, env=child_env, check=False)
            exit_code = proc.returncode
    except BaseException:
        # AIline-side failure (subprocess raise, mlflow context error, etc.)
        # after we already published an in_progress row: mark it failed so the
        # UI does not leave it stuck visually as "in progress" forever.
        poller_stop.set()
        if poller_thread is not None:
            poller_thread.join(timeout=2.0)
        if record_inserted:
            try:
                repository.fail_run(
                    commit_id,
                    exit_code=exit_code,
                    finished_at=datetime.now().isoformat(),
                    mlflow_run=mlflow_run_id,
                )
            except Exception as _exc:
                logging.debug("fail_run on aborted track failed: %s", _exc)
        raise

    until_utc = datetime.now(timezone.utc)

    if poller_thread is not None:
        poller_stop.set()
        poller_thread.join(timeout=2.0)
        if poller_result:
            mlflow_run_id = poller_result[0] or mlflow_run_id
        # Final retry: the child may have started its MLflow run very late,
        # or the last poll cycle may have raced with subprocess exit.
        if not mlflow_run_id and correlation_id:
            late_run_id = _resolve_run_by_correlation(correlation_id)
            if late_run_id and repository.set_mlflow_run(commit_id, late_run_id):
                mlflow_run_id = late_run_id
        if not mlflow_run_id:
            logging.warning(
                "AIline tag-based MLflow link did not match any run "
                "(correlation_id=%s). The user's script may have run "
                "without MLflow, or the AIline plugin was not loaded in "
                "the same Python environment.",
                correlation_id,
            )

    if (
        track_mlflow["mode"] == "inherit"
        and not mlflow_run_id
        and link_strategy != "tag"
    ):
        mlflow_run_id = _best_effort_mlflow_run_after_inherit_child(since_utc, until_utc)
    if track_mlflow["mode"] == "inherit":
        # When prelink supplied the run id, skip name sync: AIline already
        # set ``mlflow.runName`` at create_run time, and the user's script
        # may have legitimately renamed the run via mlflow.set_tag during
        # execution.
        if not prelink_run_id:
            _maybe_sync_inherit_mlflow_name(
                mlflow_run_id,
                record_label,
                str(track_mlflow.get("inherit_name_sync", "auto")),
            )

    finished_at = datetime.now().isoformat()
    if exit_code == 0:
        repository.complete_run(
            commit_id,
            exit_code=exit_code,
            mlflow_run=mlflow_run_id,
            env_fingerprint_json=env_fingerprint_json,
            env_fingerprint_status=env_fingerprint_status,
            finished_at=finished_at,
        )
    else:
        repository.fail_run(
            commit_id,
            exit_code=exit_code,
            finished_at=finished_at,
            mlflow_run=mlflow_run_id,
            env_fingerprint_json=env_fingerprint_json,
            env_fingerprint_status=env_fingerprint_status,
        )

    if record is not None:
        record.mlflow_run = mlflow_run_id
        record.exit_code = exit_code
        record.finished_at = finished_at
        record.status = (
            repository.RUN_STATUS_DONE
            if exit_code == 0
            else repository.RUN_STATUS_FAILED
        )

    return TrackResult(
        exit_code=exit_code if exit_code is not None else 1,
        commit_id=commit_id,
        commit_type=commit_type.value,
        record=record,
        snapshot_path=record.snapshot_path if record else None,
        mlflow_run_id=mlflow_run_id,
        record_name=record_label,
    )
