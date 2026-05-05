"""Tests for the content-addressed snapshot object store."""

import hashlib
import os
import tempfile
import unittest
from unittest.mock import patch

from ailine.snapshot import object_store


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class ObjectStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.storage = self.tmp.name
        self.payload_dir = os.path.join(self.tmp.name, "src")
        os.makedirs(self.payload_dir, exist_ok=True)

    def _write_file(self, name: str, data: bytes) -> tuple[str, str]:
        path = os.path.join(self.payload_dir, name)
        with open(path, "wb") as f:
            f.write(data)
        return path, _sha256_bytes(data)

    def test_object_path_uses_two_char_shard(self):
        sha = "abcdef0123456789" * 4  # 64 hex chars
        path = object_store.object_path(sha, self.storage)
        expected_dir = os.path.join(self.storage, "objects", sha[:2])
        self.assertEqual(os.path.dirname(path), expected_dir)
        self.assertTrue(path.endswith(f"{sha}.zst"))

    def test_object_path_rejects_short_sha(self):
        with self.assertRaises(ValueError):
            object_store.object_path("a", self.storage)

    def test_put_file_is_idempotent(self):
        src, sha = self._write_file("foo.txt", b"hello world\n")
        first = object_store.put_file(src, sha, self.storage)
        size_after_first = os.path.getsize(first)
        mtime_after_first = os.path.getmtime(first)
        second = object_store.put_file(src, sha, self.storage)
        self.assertEqual(first, second)
        self.assertEqual(os.path.getsize(second), size_after_first)
        self.assertEqual(os.path.getmtime(second), mtime_after_first)

    def test_round_trip_binary_safe(self):
        payload = bytes(range(256)) * 7
        src, sha = self._write_file("blob.bin", payload)
        object_store.put_file(src, sha, self.storage)
        restored = object_store.read_object_bytes(sha, self.storage)
        self.assertEqual(restored, payload)

    def test_read_object_bytes_max_bytes_caps(self):
        payload = b"x" * 4096
        src, sha = self._write_file("big.txt", payload)
        object_store.put_file(src, sha, self.storage)
        partial = object_store.read_object_bytes(sha, self.storage, max_bytes=512)
        self.assertEqual(len(partial), 512)
        self.assertTrue(partial.startswith(b"x" * 512))

    def test_read_missing_object_raises(self):
        with self.assertRaises(FileNotFoundError):
            object_store.read_object_bytes("0" * 64, self.storage)

    def test_atomic_write_does_not_leave_partial_on_error(self):
        src, sha = self._write_file("crash.txt", b"abc")

        def _boom(*_args, **_kwargs):
            raise RuntimeError("simulated zstd failure")

        with patch.object(object_store.zstd, "ZstdCompressor") as cctx_factory:
            cctx_factory.return_value.copy_stream.side_effect = _boom
            with self.assertRaises(RuntimeError):
                object_store.put_file(src, sha, self.storage)

        final = object_store.object_path(sha, self.storage)
        self.assertFalse(
            os.path.exists(final),
            msg="object must not exist when compression fails",
        )
        leftover = []
        objects_root = os.path.join(self.storage, "objects", sha[:2])
        if os.path.isdir(objects_root):
            leftover = [
                name for name in os.listdir(objects_root) if name.startswith(".tmp.")
            ]
        self.assertEqual(leftover, [], msg=f"orphan tmp file(s) left: {leftover}")


if __name__ == "__main__":
    unittest.main()
