"""Content-addressed per-file object store for snapshot payloads.

A snapshot's manifest already carries a ``sha256`` for every included file,
so we reuse that hash as the object key. Objects live under
``<storage_dir>/objects/<sha[:2]>/<sha>.zst`` and are compressed with the
same ``zstandard`` settings used for legacy ``tar.zst`` archives.

This module is intentionally tiny (SRP): it owns
on-disk placement and atomic write semantics — nothing more. Higher-level
callers ([ailine.snapshot.archive](ailine/snapshot/archive.py)) decide which
files to store and how to surface the resulting bytes back to the user.
"""

from __future__ import annotations

import os
import tempfile
from typing import Optional

import zstandard as zstd


_OBJECTS_SUBDIR = "objects"
_COMPRESS_LEVEL = 10


def _objects_root(storage_dir: str) -> str:
    return os.path.join(os.path.abspath(storage_dir), _OBJECTS_SUBDIR)


def object_path(sha256: str, storage_dir: str) -> str:
    """Return the absolute path where ``sha256`` is (or would be) stored."""
    if not sha256 or len(sha256) < 4:
        raise ValueError(f"sha256 must be a hex digest, got: {sha256!r}")
    return os.path.join(_objects_root(storage_dir), sha256[:2], f"{sha256}.zst")


def has_object(sha256: str, storage_dir: str) -> bool:
    return os.path.exists(object_path(sha256, storage_dir))


def put_file(src_path: str, sha256: str, storage_dir: str) -> str:
    """Store ``src_path`` as the object identified by ``sha256``.

    Atomic and idempotent: if the object already exists the source is not
    re-read; otherwise bytes are streamed through zstd into a temporary file
    in the destination directory and then ``os.replace``-d into final place
    so a crash mid-write cannot leave a partial object behind.
    """
    final_path = object_path(sha256, storage_dir)
    if os.path.exists(final_path):
        return final_path

    final_dir = os.path.dirname(final_path)
    os.makedirs(final_dir, exist_ok=True)

    tmp_fd, tmp_path = tempfile.mkstemp(prefix=".tmp.", suffix=".zst", dir=final_dir)
    os.close(tmp_fd)
    try:
        cctx = zstd.ZstdCompressor(level=_COMPRESS_LEVEL)
        with open(src_path, "rb") as src, open(tmp_path, "wb") as dst:
            cctx.copy_stream(src, dst)
        os.replace(tmp_path, final_path)
    except BaseException:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except OSError:
                pass
        raise
    return final_path


def read_object_bytes(sha256: str, storage_dir: str, max_bytes: Optional[int] = None) -> bytes:
    """Decompress the named object and return its bytes.

    Raises ``FileNotFoundError`` if the object is missing. ``max_bytes``
    caps the decompressed size; when set, only the first ``max_bytes``
    bytes are returned.
    """
    path = object_path(sha256, storage_dir)
    if not os.path.exists(path):
        raise FileNotFoundError(f"object not found: {path}")
    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as src:
        with dctx.stream_reader(src) as reader:
            if max_bytes is None:
                return reader.read()
            return reader.read(max_bytes)
