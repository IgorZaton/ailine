"""Snapshot ignore-spec source of truth.

`ailine track` excludes files from snapshots based on a gitignore-style
``PathSpec``. The spec is built from two layers, in order:

1. :data:`DEFAULT_AILINEIGNORE_PATTERNS` — comprehensive defaults shipped
   with AIline (Python build/cache, virtualenvs, lint/test caches, IDE and
   AI-assistant scratch dirs, mlruns/wandb/etc., DVC internals).
2. ``<repo_root>/.ailineignore`` — user file using `gitignore wildmatch`_
   syntax. May add patterns or negate defaults with ``!``.

This is the SINGLE source of truth used by both :mod:`ailine.snapshot.scan`
and :mod:`ailine.snapshot.restore`. ``snapshot.exclude_globs`` in
``.ailine.yml`` is deliberately not honoured anymore — the validator
rejects it with a migration message.

.. _`gitignore wildmatch`:
    https://git-scm.com/docs/gitignore
"""

from __future__ import annotations

import os
from typing import Iterable

import pathspec


AILINEIGNORE_FILENAME = ".ailineignore"


# Single source of truth for default ignore patterns. The tuple
# DEFAULT_AILINEIGNORE_PATTERNS is derived from this so the rendered
# .ailineignore template and the runtime spec can never drift.
_DEFAULT_SECTION_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Version control", (".git/",)),
    ("AIline internal", (".ailine/",)),
    (
        "Python build / cache / installer outputs",
        (
            "__pycache__/",
            "*.py[cod]",
            "*$py.class",
            "*.so",
            ".Python",
            "build/",
            "develop-eggs/",
            "dist/",
            "downloads/",
            "eggs/",
            ".eggs/",
            "lib/",
            "lib64/",
            "parts/",
            "sdist/",
            "var/",
            "wheels/",
            "share/python-wheels/",
            "*.egg-info/",
            ".installed.cfg",
            "*.egg",
            "MANIFEST",
        ),
    ),
    (
        "Virtualenvs",
        (".env", ".venv/", "env/", "venv/", "ENV/", "env.bak/", "venv.bak/"),
    ),
    (
        "Test / lint / type-check caches",
        (
            ".tox/",
            ".nox/",
            ".coverage",
            ".coverage.*",
            ".cache/",
            "nosetests.xml",
            "coverage.xml",
            "*.cover",
            "*.py,cover",
            ".hypothesis/",
            ".pytest_cache/",
            "htmlcov/",
            ".mypy_cache/",
            ".dmypy.json",
            "dmypy.json",
            ".pyre/",
            ".pytype/",
            ".ruff_cache/",
            "cython_debug/",
        ),
    ),
    (
        "Jupyter / IPython",
        (".ipynb_checkpoints/", "profile_default/", "ipython_config.py"),
    ),
    (
        "Editors / IDEs",
        (".idea/", ".vscode/", "*.swp", "*.swo", ".DS_Store", "Thumbs.db"),
    ),
    (
        "AI assistants / agent tooling",
        (
            ".cursor/",
            ".continue/",
            ".aider*",
            ".claude/",
            ".codeium/",
            ".tabnine/",
            ".windsurf/",
            ".codex/",
        ),
    ),
    (
        "ML / experiment tracking",
        (
            "mlruns/",
            "mlartifacts/",
            "wandb/",
            "runs/",
            "lightning_logs/",
            "tensorboard/",
            ".tensorboard-info/",
            "checkpoints/",
            "ray_results/",
        ),
    ),
    ("DVC internals", (".dvc/cache/", ".dvc/tmp/", ".dvc/plots/")),
)


DEFAULT_AILINEIGNORE_PATTERNS: tuple[str, ...] = tuple(
    pattern for _, group in _DEFAULT_SECTION_GROUPS for pattern in group
)


def _strip_comments_and_blanks(lines: Iterable[str]) -> list[str]:
    cleaned: list[str] = []
    for raw in lines:
        line = raw.rstrip("\n").rstrip("\r")
        stripped = line.lstrip()
        if not stripped or stripped.startswith("#"):
            continue
        cleaned.append(line)
    return cleaned


def _read_user_patterns(repo_root: str) -> list[str]:
    path = os.path.join(repo_root, AILINEIGNORE_FILENAME)
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return _strip_comments_and_blanks(f)


def load_ignore_spec(repo_root: str) -> pathspec.PathSpec:
    """Build the active ignore ``PathSpec`` for ``repo_root``.

    Defaults are always applied; user patterns from ``.ailineignore`` are
    appended in order so ``!``-negations correctly override them.
    """
    patterns: list[str] = list(DEFAULT_AILINEIGNORE_PATTERNS)
    patterns.extend(_read_user_patterns(repo_root))
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def is_ignored(rel_path: str, spec: pathspec.PathSpec) -> bool:
    """Return ``True`` when ``rel_path`` matches the active ignore ``spec``.

    ``rel_path`` may use OS-native separators; it is normalised to POSIX
    before matching to keep behaviour stable across platforms.
    """
    if not rel_path:
        return False
    normalised = rel_path.replace(os.sep, "/")
    return spec.match_file(normalised)


def render_default_ailineignore() -> str:
    """Return a header + the canonical default content for ``.ailineignore``.

    Used by :func:`ailine.cli.init._write_default_ailineignore` so the file
    seeded by ``ailine init-workspace`` and ``ailine init-demo`` reflects
    exactly the patterns that are also active in code.
    """
    header = (
        "# Generated by `ailine init-workspace` / `ailine init-demo`.\n"
        "# Gitignore-style syntax (parsed via pathspec). The patterns below\n"
        "# mirror DEFAULT_AILINEIGNORE_PATTERNS shipped with AIline; they are\n"
        "# always active even when this file is missing. Add your own lines\n"
        "# below or use `!pattern` to negate a default.\n"
        "\n"
    )
    body_lines: list[str] = []
    for title, patterns in _DEFAULT_SECTION_GROUPS:
        body_lines.append(f"# --- {title} ---")
        body_lines.extend(patterns)
        body_lines.append("")
    return header + "\n".join(body_lines).rstrip() + "\n"
