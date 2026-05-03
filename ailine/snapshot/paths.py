"""Pure path/text helpers used across snapshot, linkage, and web layers."""

import fnmatch
import hashlib
import os
from typing import List


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_rel_path(path: str) -> str:
    return path.replace(os.sep, "/")


def is_excluded(rel_path: str, exclude_globs: List[str]) -> bool:
    norm = normalize_rel_path(rel_path)
    for pattern in exclude_globs:
        if fnmatch.fnmatch(norm, pattern):
            return True
    return False


def ensure_utf8_text(content: str) -> str:
    """Sanitize potentially invalid UTF-8 (e.g. surrogates) coming from git output."""
    return content.encode("utf-8", errors="replace").decode("utf-8")
