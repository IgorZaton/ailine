"""Shared helpers for the code-browser routes (commit + snapshot)."""

import posixpath
from typing import Iterable, List, Optional

# Hard cap on bytes returned for an inline blob view; larger files are truncated
# with a banner. Kept generous for typical source files; binary files generally
# fail UTF-8 decoding upstream and never reach here.
MAX_BLOB_BYTES = 512 * 1024


_LANGUAGE_BY_EXT = {
    "py": "python",
    "pyi": "python",
    "js": "javascript",
    "mjs": "javascript",
    "ts": "typescript",
    "tsx": "typescript",
    "jsx": "javascript",
    "json": "json",
    "yaml": "yaml",
    "yml": "yaml",
    "toml": "toml",
    "md": "markdown",
    "rst": "restructuredtext",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "html": "xml",
    "xml": "xml",
    "css": "css",
    "scss": "scss",
    "sql": "sql",
    "ini": "ini",
    "cfg": "ini",
    "dockerfile": "dockerfile",
}


def detect_language(path: str) -> str:
    """Return a highlight.js language hint for ``path`` (empty if unknown)."""
    name = posixpath.basename(path).lower()
    if name in {"dockerfile", "makefile"}:
        return _LANGUAGE_BY_EXT.get(name, "")
    _, _, ext = name.rpartition(".")
    return _LANGUAGE_BY_EXT.get(ext, "")


def safe_relpath(raw: Optional[str], allowed: Iterable[str]) -> Optional[str]:
    """Validate a user-supplied relative path against an allow-list.

    Returns the normalized path on success or ``None`` if the path is missing,
    absolute, contains parent traversal, or is not present in ``allowed``.
    """
    if not raw:
        return None
    normalized = posixpath.normpath(raw)
    if normalized.startswith("/") or normalized == "." or normalized == "..":
        return None
    if any(part in {"..", ""} for part in normalized.split("/")):
        return None
    allowed_set = allowed if isinstance(allowed, set) else set(allowed)
    return normalized if normalized in allowed_set else None


def truncate_text(content: str, max_bytes: int = MAX_BLOB_BYTES) -> tuple[str, bool]:
    """Truncate text to ``max_bytes`` UTF-8 bytes; return ``(text, was_truncated)``."""
    encoded = content.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return content, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def build_path_tree(paths: List[str]) -> List[dict]:
    """Build a nested tree structure from a flat sorted list of POSIX paths.

    Each node is ``{"name": str, "path": str | None, "type": "dir"|"file",
    "children": list}``. Directories sort before files; both alphabetical.
    """
    root: dict = {"name": "", "path": "", "type": "dir", "children": {}}
    for path in paths:
        parts = path.split("/")
        cursor = root
        for idx, part in enumerate(parts):
            is_file = idx == len(parts) - 1
            child = cursor["children"].get(part)
            if child is None:
                child = {
                    "name": part,
                    "path": path if is_file else "/".join(parts[: idx + 1]),
                    "type": "file" if is_file else "dir",
                    "children": {},
                }
                cursor["children"][part] = child
            cursor = child

    def _materialize(node: dict) -> dict:
        children = list(node["children"].values())
        children.sort(key=lambda n: (n["type"] == "file", n["name"].lower()))
        return {
            "name": node["name"],
            "path": node["path"] if node["type"] == "file" else None,
            "type": node["type"],
            "children": [_materialize(c) for c in children] if node["type"] == "dir" else [],
        }

    return _materialize(root)["children"]
