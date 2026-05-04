"""Unit tests for the strict-sync restore engine."""

import hashlib
import os
import tempfile
import unittest

from ailine.snapshot import object_store
from ailine.snapshot.restore import (
    _is_safe_relative,
    apply_restore,
    collect_restore_entries,
    plan_restore,
)


def _put(storage: str, payload: bytes) -> str:
    sha = hashlib.sha256(payload).hexdigest()
    src_dir = os.path.join(storage, "_src")
    os.makedirs(src_dir, exist_ok=True)
    src_path = os.path.join(src_dir, sha)
    with open(src_path, "wb") as f:
        f.write(payload)
    object_store.put_file(src_path, sha, storage)
    os.remove(src_path)
    return sha


class IsSafeRelativeTests(unittest.TestCase):
    def test_rejects_absolute_paths(self):
        self.assertFalse(_is_safe_relative("/etc/passwd"))

    def test_rejects_traversal(self):
        self.assertFalse(_is_safe_relative("../etc/passwd"))
        self.assertFalse(_is_safe_relative("foo/../../etc"))

    def test_rejects_empty_and_dot(self):
        self.assertFalse(_is_safe_relative(""))
        self.assertFalse(_is_safe_relative("."))

    def test_accepts_normal_paths(self):
        self.assertTrue(_is_safe_relative("foo.py"))
        self.assertTrue(_is_safe_relative("a/b/c.txt"))


class CollectRestoreEntriesTests(unittest.TestCase):
    def test_only_include_classification_is_restored(self):
        manifest = [
            {
                "path": "a.py",
                "classification": "include",
                "decision": "include",
                "sha256": "a" * 64,
            },
            {
                "path": "b.bin",
                "classification": "large-non-dvc",
                "decision": "skip",
                "sha256": "b" * 64,
            },
            {
                "path": "c.dvc.json",
                "classification": "large-and-dvc",
                "decision": "pointer",
                "sha256": "c" * 64,
            },
            {
                "path": "secrets.env",
                "classification": "excluded-by-policy",
                "decision": "skip",
                "sha256": "d" * 64,
            },
        ]
        restore, skipped = collect_restore_entries(manifest)
        self.assertEqual([e.rel_path for e in restore], ["a.py"])
        self.assertEqual(set(skipped), {"b.bin", "c.dvc.json"})

    def test_unsafe_path_raises(self):
        manifest = [
            {
                "path": "../etc/passwd",
                "classification": "include",
                "decision": "include",
                "sha256": "a" * 64,
            }
        ]
        with self.assertRaises(ValueError):
            collect_restore_entries(manifest)


class PlanRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.storage = os.path.join(self.repo, ".ailine", "snapshots")
        os.makedirs(self.repo)
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, ".ailine"))

    def test_plan_writes_and_deletions(self):
        sha_a = _put(self.storage, b"contents-a\n")
        with open(os.path.join(self.repo, "extra.py"), "w") as f:
            f.write("drift\n")
        with open(os.path.join(self.repo, ".git", "HEAD"), "w") as f:
            f.write("ref: refs/heads/main\n")

        manifest = [
            {
                "path": "a.py",
                "classification": "include",
                "decision": "include",
                "sha256": sha_a,
            }
        ]
        plan = plan_restore(manifest, storage_dir=self.storage, repo_root=self.repo)
        self.assertEqual([e.rel_path for e in plan.writes], ["a.py"])
        self.assertEqual(plan.missing_objects, [])
        self.assertIn("extra.py", plan.deletions)
        # .git contents must never appear in the deletion list.
        self.assertFalse(any(p.startswith(".git/") for p in plan.deletions))

    def test_missing_objects_are_reported(self):
        manifest = [
            {
                "path": "a.py",
                "classification": "include",
                "decision": "include",
                "sha256": "f" * 64,
            }
        ]
        plan = plan_restore(manifest, storage_dir=self.storage, repo_root=self.repo)
        self.assertEqual([e.rel_path for e in plan.missing_objects], ["a.py"])
        self.assertEqual(plan.writes, [])


class ApplyRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.storage = os.path.join(self.repo, ".ailine", "snapshots")
        os.makedirs(self.repo)

    def test_atomic_write_creates_files_and_removes_extras(self):
        sha = _put(self.storage, b"hello\n")
        with open(os.path.join(self.repo, "to_delete.txt"), "w") as f:
            f.write("bye\n")

        manifest = [
            {
                "path": "nested/dir/keep.txt",
                "classification": "include",
                "decision": "include",
                "sha256": sha,
            }
        ]
        plan = plan_restore(manifest, storage_dir=self.storage, repo_root=self.repo)
        apply_restore(plan, storage_dir=self.storage, repo_root=self.repo)

        kept = os.path.join(self.repo, "nested", "dir", "keep.txt")
        self.assertTrue(os.path.exists(kept))
        with open(kept, "rb") as f:
            self.assertEqual(f.read(), b"hello\n")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "to_delete.txt")))

    def test_apply_refuses_when_objects_missing(self):
        manifest = [
            {
                "path": "x.py",
                "classification": "include",
                "decision": "include",
                "sha256": "0" * 64,
            }
        ]
        plan = plan_restore(manifest, storage_dir=self.storage, repo_root=self.repo)
        with self.assertRaises(RuntimeError):
            apply_restore(plan, storage_dir=self.storage, repo_root=self.repo)


if __name__ == "__main__":
    unittest.main()
