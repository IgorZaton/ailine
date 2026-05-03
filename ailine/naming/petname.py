"""Default ``adjective-animal`` names and validation for ``--name``."""

from __future__ import annotations

import re
import secrets
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent / "data"

# Letters, digits, spaces, common punctuation; 1–120 chars after strip.
_RECORD_NAME_RE = re.compile(r"^[\w .,'+/#()-]{1,120}$", re.UNICODE)


@lru_cache(maxsize=1)
def _word_lines(filename: str) -> tuple[str, ...]:
    path = _DATA / filename
    lines = tuple(
        ln.strip().lower()
        for ln in path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    )
    if len(lines) != 256:
        raise RuntimeError(
            f"Expected 256 lines in {path}, found {len(lines)} (rebuild word lists)."
        )
    return lines


def default_record_name() -> str:
    """Return ``<adjective>-<animal>`` using 256×256 fixed lists and ``secrets``."""
    adjectives = _word_lines("adjectives256.txt")
    animals = _word_lines("animals256.txt")
    a = adjectives[secrets.randbelow(256)]
    b = animals[secrets.randbelow(256)]
    return f"{a}-{b}"


def validate_record_name(raw: str) -> str:
    """Return a stripped, validated display name or raise ``ValueError``."""
    s = raw.strip()
    if not s:
        raise ValueError("Record name cannot be empty or whitespace-only.")
    if "\n" in s or "\r" in s or "\t" in s:
        raise ValueError("Record name cannot contain newlines or tabs.")
    if not _RECORD_NAME_RE.fullmatch(s):
        raise ValueError(
            "Record name must be 1–120 characters: letters, digits, spaces, "
            "and limited punctuation (word chars, space, . , ' + / - # ( ) )."
        )
    return s
