"""Tests for ``ailine prune-legacy-snapshots``."""

import json
import os
import sqlite3
import tempfile
import unittest

from click.testing import CliRunner

from ailine.cli.prune import prune_legacy_snapshots_command
from ailine.config import constants
from ailine.persistence.db import init_db


def _insert_snapshot_row(
    db_path: str,
    *,
    row_id: str,
    parent: str | None,
    snapshot_path: str | None,
    manifest_path: str | None,
    diff_path: str | None,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO tree (id, type, parent, snapshot_path, manifest_path, diff_path, "
            "timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row_id, "snapshot", parent, snapshot_path, manifest_path, diff_path, "2026-05-04T00:00:00"),
        )
        conn.commit()
    finally:
        conn.close()


def _write_objects_v1(storage: str, snap_id: str) -> tuple[str, str, str]:
    manifest = os.path.join(storage, f"{snap_id}.manifest.json")
    metadata = os.path.join(storage, f"{snap_id}.metadata.json")
    diff = os.path.join(storage, f"{snap_id}.diff.patch")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(metadata, "w", encoding="utf-8") as f:
        json.dump({"format": "objects-v1", "objects_dir": os.path.join(storage, "objects")}, f)
    with open(diff, "w", encoding="utf-8") as f:
        f.write("")
    return manifest, metadata, diff


def _write_legacy_no_format(storage: str, snap_id: str) -> tuple[str, str, str]:
    """Legacy bundle: metadata exists but lacks the ``format`` field."""
    manifest = os.path.join(storage, f"{snap_id}.manifest.json")
    metadata = os.path.join(storage, f"{snap_id}.metadata.json")
    diff = os.path.join(storage, f"{snap_id}.diff.patch")
    with open(manifest, "w", encoding="utf-8") as f:
        json.dump([], f)
    with open(metadata, "w", encoding="utf-8") as f:
        json.dump({"snapshot_id": snap_id}, f)
    with open(diff, "w", encoding="utf-8") as f:
        f.write("")
    return manifest, metadata, diff


def _write_legacy_tar_only(storage: str, snap_id: str) -> str:
    """Legacy bundle: only a tar.zst payload remains, metadata is gone."""
    tar_path = os.path.join(storage, f"{snap_id}.tar.zst")
    with open(tar_path, "wb") as f:
        f.write(b"\x28\xb5\x2f\xfd")
    return tar_path


class PruneLegacySnapshotsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_db_path = constants.DB_PATH
        constants.DB_PATH = os.path.join(self.tmp.name, "ailine_tree.db")
        init_db()
        self.storage = os.path.join(self.tmp.name, "snapshots")
        os.makedirs(self.storage, exist_ok=True)

    def tearDown(self):
        constants.DB_PATH = self.old_db_path

    def _row_count(self) -> int:
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM tree WHERE type = 'snapshot'")
            return cur.fetchone()[0]
        finally:
            conn.close()

    def _row_ids(self) -> set[str]:
        conn = sqlite3.connect(constants.DB_PATH)
        try:
            cur = conn.cursor()
            cur.execute("SELECT id FROM tree WHERE type = 'snapshot'")
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    def test_legacy_metadata_without_format_is_removed(self):
        manifest, metadata, diff = _write_legacy_no_format(self.storage, "legacy1")
        _insert_snapshot_row(
            constants.DB_PATH,
            row_id="legacy1",
            parent="parentA",
            snapshot_path=None,
            manifest_path=manifest,
            diff_path=diff,
        )

        runner = CliRunner()
        result = runner.invoke(prune_legacy_snapshots_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(self._row_count(), 0)
        self.assertFalse(os.path.exists(manifest))
        self.assertFalse(os.path.exists(metadata))
        self.assertFalse(os.path.exists(diff))
        self.assertIn("pruned 1 rows", result.output)

    def test_tar_only_legacy_row_is_removed_when_metadata_missing(self):
        tar_path = _write_legacy_tar_only(self.storage, "legacy2")
        _insert_snapshot_row(
            constants.DB_PATH,
            row_id="legacy2",
            parent="parentB",
            snapshot_path=tar_path,
            manifest_path=None,
            diff_path=None,
        )

        runner = CliRunner()
        result = runner.invoke(prune_legacy_snapshots_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(self._row_count(), 0)
        self.assertFalse(os.path.exists(tar_path))

    def test_objects_v1_row_is_kept(self):
        manifest, metadata, diff = _write_objects_v1(self.storage, "snapok1")
        _insert_snapshot_row(
            constants.DB_PATH,
            row_id="snapok1",
            parent="parentC",
            snapshot_path=None,
            manifest_path=manifest,
            diff_path=diff,
        )

        runner = CliRunner()
        result = runner.invoke(prune_legacy_snapshots_command, [])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(self._row_ids(), {"snapok1"})
        self.assertTrue(os.path.exists(manifest))
        self.assertTrue(os.path.exists(metadata))
        self.assertTrue(os.path.exists(diff))

    def test_dry_run_lists_but_does_not_modify(self):
        manifest, metadata, diff = _write_legacy_no_format(self.storage, "legacy3")
        _insert_snapshot_row(
            constants.DB_PATH,
            row_id="legacy3",
            parent="parentD",
            snapshot_path=None,
            manifest_path=manifest,
            diff_path=diff,
        )
        ok_manifest, ok_metadata, ok_diff = _write_objects_v1(self.storage, "snapok2")
        _insert_snapshot_row(
            constants.DB_PATH,
            row_id="snapok2",
            parent="parentE",
            snapshot_path=None,
            manifest_path=ok_manifest,
            diff_path=ok_diff,
        )

        runner = CliRunner()
        result = runner.invoke(prune_legacy_snapshots_command, ["--dry-run"])
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertIn("legacy3", result.output)
        self.assertIn("would prune", result.output)
        self.assertIn("dry-run summary", result.output)

        self.assertEqual(self._row_ids(), {"legacy3", "snapok2"})
        self.assertTrue(os.path.exists(manifest))
        self.assertTrue(os.path.exists(metadata))
        self.assertTrue(os.path.exists(diff))
        self.assertTrue(os.path.exists(ok_manifest))
        self.assertTrue(os.path.exists(ok_metadata))
        self.assertTrue(os.path.exists(ok_diff))


if __name__ == "__main__":
    unittest.main()
