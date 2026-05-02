"""Split a unified git patch into per-file sections for the web diff viewer."""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass(frozen=True)
class DiffSection:
    """One file's chunk from a unified diff."""

    title: str
    body: str


def _strip_git_path(prefixes: tuple[str, ...], raw: str) -> str:
    for p in prefixes:
        if raw.startswith(p):
            return raw[len(p) :]
    return raw


def _title_from_lines(lines: list[str]) -> str:
    """Best-effort display path for a diff section."""
    plus_line = None
    minus_line = None
    for line in lines:
        if line.startswith("diff --git "):
            tail = line[len("diff --git ") :].strip()
            try:
                parts = shlex.split(tail)
            except ValueError:
                parts = tail.split()
            if len(parts) >= 2:
                right = parts[-1].strip('"')
                if right.startswith("b/"):
                    return right[2:]
                return right
            if len(parts) == 1:
                p = parts[0].strip('"')
                if p.startswith("b/"):
                    return p[2:]
                return p
        if line.startswith("+++ "):
            plus_line = line[4:].strip().strip('"')
        if line.startswith("--- "):
            minus_line = line[4:].strip().strip('"')

    # Prefer destination path; fall back for pure add/delete hunks.
    if plus_line and plus_line not in ("/dev/null", "dev/null"):
        return _strip_git_path(("b/",), plus_line)
    if minus_line and minus_line not in ("/dev/null", "dev/null"):
        return _strip_git_path(("a/",), minus_line) + " (deleted)"
    return "patch"


def split_unified_diff(text: str) -> list[DiffSection]:
    """Split ``text`` into sections, one per ``diff --git`` file block.

    If there is no ``diff --git`` header (legacy single-file patch), the whole
    patch is returned as a single section.
    """
    if not text or not text.strip():
        return []
    lines = text.splitlines()
    chunks: list[list[str]] = []
    buf: list[str] = []
    for line in lines:
        if line.startswith("diff --git ") and buf:
            chunks.append(buf)
            buf = [line]
        else:
            buf.append(line)
    if buf:
        chunks.append(buf)

    out: list[DiffSection] = []
    for chunk_lines in chunks:
        body = "\n".join(chunk_lines)
        title = _title_from_lines(chunk_lines)
        out.append(DiffSection(title=title, body=body))
    return out
