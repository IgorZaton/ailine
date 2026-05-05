"""Narrow repository facade over the ``tree`` SQLite table.

This isolates SQL details from CLI/web layers so they only deal with plain
dicts and dataclass-like records.
"""

import json
import os
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from ailine.config import constants


# Status enum constants; kept lower-case strings so existing rows (where the
# column is NULL because they predate the lifecycle feature) are interpreted
# as "done" by the UI without needing a backfill migration.
RUN_STATUS_IN_PROGRESS = "in_progress"
RUN_STATUS_DONE = "done"
RUN_STATUS_FAILED = "failed"


_INSERT_SQL = """INSERT OR REPLACE INTO tree
    (id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url,
     manifest_path, metadata_path, archive_bytes, included_file_count, excluded_file_count,
     large_file_pointer_count, diff_path, dvc_linkage_json, dvc_linkage_status,
     env_fingerprint_json, env_fingerprint_status, run_command_json, run_command_summary,
     record_name, status, started_at, finished_at, exit_code)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


@dataclass
class RunRecord:
    id: str
    type: str
    parent: Optional[str]
    mlflow_run: Optional[str]
    dvc_version: Optional[str]
    snapshot_path: Optional[str]
    timestamp: str
    git_url: Optional[str]
    manifest_path: Optional[str] = None
    metadata_path: Optional[str] = None
    archive_bytes: Optional[int] = None
    included_file_count: Optional[int] = None
    excluded_file_count: Optional[int] = None
    large_file_pointer_count: Optional[int] = None
    diff_path: Optional[str] = None
    dvc_linkage_json: Optional[str] = None
    dvc_linkage_status: Optional[str] = None
    env_fingerprint_json: Optional[str] = None
    env_fingerprint_status: Optional[str] = None
    run_command_json: Optional[str] = None
    run_command_summary: Optional[str] = None
    record_name: Optional[str] = None
    status: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    exit_code: Optional[int] = None

    def as_row(self) -> tuple:
        return (
            self.id,
            self.type,
            self.parent,
            self.mlflow_run,
            self.dvc_version,
            self.snapshot_path,
            self.timestamp,
            self.git_url,
            self.manifest_path,
            self.metadata_path,
            self.archive_bytes,
            self.included_file_count,
            self.excluded_file_count,
            self.large_file_pointer_count,
            self.diff_path,
            self.dvc_linkage_json,
            self.dvc_linkage_status,
            self.env_fingerprint_json,
            self.env_fingerprint_status,
            self.run_command_json,
            self.run_command_summary,
            self.record_name,
            self.status,
            self.started_at,
            self.finished_at,
            self.exit_code,
        )


def _connect(db_path: Optional[str]) -> sqlite3.Connection:
    return sqlite3.connect(db_path or constants.DB_PATH)


def insert_run(record: RunRecord, db_path: Optional[str] = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute(_INSERT_SQL, record.as_row())
        conn.commit()
    finally:
        conn.close()


def insert_running_run(record: RunRecord, db_path: Optional[str] = None) -> None:
    """Insert a row in ``in_progress`` state.

    Forces ``status = "in_progress"`` and ensures ``started_at`` is set so the
    UI/CLI can show the run as live the moment ``ailine track`` resolves the
    record id but before the child process completes. Subsequent finalization
    goes through :func:`complete_run` or :func:`fail_run`, which update only
    the dynamic fields known after the subprocess exits.
    """
    record.status = RUN_STATUS_IN_PROGRESS
    if not record.started_at:
        record.started_at = record.timestamp
    insert_run(record, db_path)


def complete_run(
    run_id: str,
    *,
    exit_code: int,
    mlflow_run: Optional[str],
    env_fingerprint_json: Optional[str],
    env_fingerprint_status: Optional[str],
    finished_at: str,
    db_path: Optional[str] = None,
) -> None:
    """Finalize a previously inserted ``in_progress`` row as ``done``.

    Only the fields that are not knowable before the child process runs are
    updated; everything else (snapshot info, manifest paths, names, dvc
    linkage, run command) was written by :func:`insert_running_run`.
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE tree SET status = ?, exit_code = ?, mlflow_run = ?, "
            "env_fingerprint_json = ?, env_fingerprint_status = ?, "
            "finished_at = ? WHERE id = ?",
            (
                RUN_STATUS_DONE,
                exit_code,
                mlflow_run,
                env_fingerprint_json,
                env_fingerprint_status,
                finished_at,
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def fail_run(
    run_id: str,
    *,
    exit_code: Optional[int],
    finished_at: str,
    mlflow_run: Optional[str] = None,
    env_fingerprint_json: Optional[str] = None,
    env_fingerprint_status: Optional[str] = None,
    db_path: Optional[str] = None,
) -> None:
    """Finalize a previously inserted ``in_progress`` row as ``failed``.

    Used for non-zero child exit codes and for AIline-side errors that occur
    after the lifecycle row was inserted. ``mlflow_run`` and the env
    fingerprint fields are accepted so we can keep them when the child *did*
    run (non-zero exit) and skip them when the failure happened earlier.
    """
    conn = _connect(db_path)
    try:
        conn.execute(
            "UPDATE tree SET status = ?, exit_code = ?, mlflow_run = COALESCE(?, mlflow_run), "
            "env_fingerprint_json = COALESCE(?, env_fingerprint_json), "
            "env_fingerprint_status = COALESCE(?, env_fingerprint_status), "
            "finished_at = ? WHERE id = ?",
            (
                RUN_STATUS_FAILED,
                exit_code,
                mlflow_run,
                env_fingerprint_json,
                env_fingerprint_status,
                finished_at,
                run_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def set_mlflow_run(
    run_id: str,
    mlflow_run_id: str,
    db_path: Optional[str] = None,
) -> bool:
    """Attach an MLflow run id to an existing lineage row, only if not already set.

    Used by the tag-based correlation poller to update the row mid-flight the
    moment MLflow surfaces a run carrying our correlation tag. The
    ``mlflow_run IS NULL OR mlflow_run = ''`` guard prevents stomping a value
    that the user's script (or the prelink path) may have already supplied.

    Returns ``True`` when the row was updated.
    """
    if not mlflow_run_id:
        return False
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            "UPDATE tree SET mlflow_run = ? "
            "WHERE id = ? AND (mlflow_run IS NULL OR mlflow_run = '')",
            (mlflow_run_id, run_id),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def fetch_status_rows(db_path: Optional[str] = None) -> List[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, "
            "git_url, dvc_linkage_json, dvc_linkage_status, env_fingerprint_json, "
            "env_fingerprint_status, run_command_json, run_command_summary, record_name, "
            "status, started_at, finished_at, exit_code FROM tree"
        )
        rows = cur.fetchall()
    finally:
        conn.close()

    result: List[dict] = []
    for r in rows:
        linkage_payload = json.loads(r[8]) if r[8] else {"items": []}
        env_payload = json.loads(r[10]) if r[10] else {}
        run_command_payload = json.loads(r[12]) if r[12] else {}
        result.append(
            {
                "id": r[0],
                "type": r[1],
                "parent": r[2],
                "mlflow_run": r[3],
                "dvc_version": r[4],
                "snapshot_path": r[5],
                "timestamp": r[6],
                "git_url": r[7],
                "dvc_linkage_status": r[9] or "missing",
                "dvc_linkage_count": len(linkage_payload.get("items", [])),
                "dvc_linkage_items": linkage_payload.get("items", []),
                "env_fingerprint_status": r[11] or "missing",
                "env_fingerprint": env_payload,
                "run_command_summary": r[13],
                "run_command_payload": run_command_payload,
                "record_name": r[14],
                "status": r[15] or RUN_STATUS_DONE,
                "started_at": r[16],
                "finished_at": r[17],
                "exit_code": r[18],
            }
        )
    return result


def fetch_commits_overview(db_path: Optional[str] = None) -> List[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, "
            "timestamp, git_url, run_command_summary, dvc_linkage_status, "
            "env_fingerprint_status, record_name, status, started_at, "
            "finished_at, exit_code FROM tree"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "type": r[1],
            "parent": r[2],
            "mlflow_run": r[3],
            "dvc_version": r[4],
            "snapshot_path": r[5],
            "timestamp": r[6],
            "git_url": r[7],
            "run_command_summary": r[8],
            "dvc_linkage_status": r[9],
            "env_fingerprint_status": r[10],
            "record_name": r[11],
            "status": r[12] or RUN_STATUS_DONE,
            "started_at": r[13],
            "finished_at": r[14],
            "exit_code": r[15],
        }
        for r in rows
    ]


def fetch_git_url(commit_id: str, db_path: Optional[str] = None) -> Optional[str]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT git_url FROM tree WHERE id = ?", (commit_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    return row[0] if row else None


def fetch_snapshot_location(snapshot_id: str, db_path: Optional[str] = None):
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT snapshot_path, parent FROM tree WHERE id = ?", (snapshot_id,))
        row = cur.fetchone()
    finally:
        conn.close()
    return row


def fetch_snapshot_browser_row(
    snapshot_id: str, db_path: Optional[str] = None
) -> Optional[dict]:
    """Fetch fields needed by the snapshot code browser (tree + blob + diff)."""
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT snapshot_path, parent, manifest_path, diff_path "
            "FROM tree WHERE id = ?",
            (snapshot_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "snapshot_path": row[0],
        "parent": row[1],
        "manifest_path": row[2],
        "diff_path": row[3],
    }


def fetch_snapshot_restore_row(
    snapshot_id: str, db_path: Optional[str] = None
) -> Optional[dict]:
    """Fetch the minimal fields ``ailine restore`` needs to materialize a snapshot.

    Returns ``None`` when the row does not exist or is not a snapshot row;
    callers translate that to a user-facing fail-fast error.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, parent, manifest_path, metadata_path, timestamp "
            "FROM tree WHERE id = ?",
            (snapshot_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row or row[1] != "snapshot":
        return None
    return {
        "id": row[0],
        "type": row[1],
        "parent": row[2],
        "manifest_path": row[3],
        "metadata_path": row[4],
        "timestamp": row[5],
    }


def fetch_record_for_remove(
    record_id: str, db_path: Optional[str] = None
) -> Optional[dict]:
    """Fetch the minimal columns ``ailine remove`` needs to clean up a row.

    Returns ``None`` when the row does not exist. Works for both ``git`` and
    ``snapshot`` rows; the on-disk artifact paths are only populated for
    snapshot rows in practice.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, mlflow_run, manifest_path, metadata_path, "
            "diff_path, record_name FROM tree WHERE id = ?",
            (record_id,),
        )
        row = cur.fetchone()
    finally:
        conn.close()
    if not row:
        return None
    return {
        "id": row[0],
        "type": row[1],
        "mlflow_run": row[2],
        "manifest_path": row[3],
        "metadata_path": row[4],
        "diff_path": row[5],
        "record_name": row[6],
    }


def fetch_all_snapshot_locations(db_path: Optional[str] = None) -> List[dict]:
    """Return on-disk locations for every snapshot row.

    Used by ``ailine prune-legacy-snapshots`` to inspect the format of each
    bundle without coupling the CLI to raw SQL.
    """
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, parent, snapshot_path, manifest_path, metadata_path, diff_path "
            "FROM tree WHERE type = 'snapshot'"
        )
        rows = cur.fetchall()
    finally:
        conn.close()
    return [
        {
            "id": r[0],
            "parent": r[1],
            "snapshot_path": r[2],
            "manifest_path": r[3],
            "metadata_path": r[4],
            "diff_path": r[5],
        }
        for r in rows
    ]


def count_rows(db_path: Optional[str] = None) -> int:
    """Return the number of rows in ``tree`` (used by ``ailine purge`` summary)."""
    if db_path is None:
        db_path = constants.DB_PATH
    if not os.path.exists(db_path):
        return 0
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM tree")
        return int(cur.fetchone()[0])
    finally:
        conn.close()


def delete_run(run_id: str, db_path: Optional[str] = None) -> None:
    conn = _connect(db_path)
    try:
        conn.execute("DELETE FROM tree WHERE id = ?", (run_id,))
        conn.commit()
    finally:
        conn.close()
