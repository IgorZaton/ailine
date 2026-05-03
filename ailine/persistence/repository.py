"""Narrow repository facade over the ``tree`` SQLite table.

This isolates SQL details from CLI/web layers so they only deal with plain
dicts and dataclass-like records.
"""

import json
import sqlite3
from dataclasses import dataclass
from typing import List, Optional

from ailine.config import constants


_INSERT_SQL = """INSERT OR REPLACE INTO tree
    (id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url,
     manifest_path, metadata_path, archive_bytes, included_file_count, excluded_file_count,
     large_file_pointer_count, diff_path, dvc_linkage_json, dvc_linkage_status,
     env_fingerprint_json, env_fingerprint_status, run_command_json, run_command_summary)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


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


def fetch_status_rows(db_path: Optional[str] = None) -> List[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, "
            "git_url, dvc_linkage_json, dvc_linkage_status, env_fingerprint_json, "
            "env_fingerprint_status, run_command_json, run_command_summary FROM tree"
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
            }
        )
    return result


def fetch_commits_overview(db_path: Optional[str] = None) -> List[dict]:
    conn = _connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, "
            "timestamp, git_url FROM tree"
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
