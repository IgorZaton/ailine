"""SQLite schema bootstrap and additive migrations for the ``tree`` table."""

import logging
import os
import sqlite3
from typing import Optional

from ailine.config import constants


_ADDITIVE_COLUMNS = {
    "manifest_path": "TEXT",
    "metadata_path": "TEXT",
    "archive_bytes": "INTEGER",
    "included_file_count": "INTEGER",
    "excluded_file_count": "INTEGER",
    "large_file_pointer_count": "INTEGER",
    "diff_path": "TEXT",
    "dvc_linkage_json": "TEXT",
    "dvc_linkage_status": "TEXT",
    "env_fingerprint_json": "TEXT",
    "env_fingerprint_status": "TEXT",
    "run_command_json": "TEXT",
    "run_command_summary": "TEXT",
    "record_name": "TEXT",
}


def _resolve_db_path(db_path: Optional[str]) -> str:
    return db_path or constants.DB_PATH


def init_db(db_path: Optional[str] = None) -> None:
    path = _resolve_db_path(db_path)
    if os.path.exists(path):
        logging.info(f"Database found at {path}")
    conn = sqlite3.connect(path)
    try:
        c = conn.cursor()
        c.execute(
            """CREATE TABLE IF NOT EXISTS tree (
                id TEXT PRIMARY KEY,
                type TEXT,
                parent TEXT,
                mlflow_run TEXT,
                dvc_version TEXT,
                snapshot_path TEXT,
                timestamp TEXT,
                git_url TEXT,
                manifest_path TEXT,
                metadata_path TEXT,
                archive_bytes INTEGER,
                included_file_count INTEGER,
                excluded_file_count INTEGER,
                large_file_pointer_count INTEGER,
                diff_path TEXT,
                dvc_linkage_json TEXT,
                dvc_linkage_status TEXT,
                env_fingerprint_json TEXT,
                env_fingerprint_status TEXT,
                run_command_json TEXT,
                run_command_summary TEXT
            )"""
        )
        c.execute("PRAGMA table_info(tree)")
        existing_columns = {row[1] for row in c.fetchall()}
        for name, col_type in _ADDITIVE_COLUMNS.items():
            if name not in existing_columns:
                c.execute(f"ALTER TABLE tree ADD COLUMN {name} {col_type}")
        conn.commit()
    finally:
        conn.close()
    logging.info("Database initialized")


def connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    return sqlite3.connect(_resolve_db_path(db_path))
