"""``/snapshot/<id>`` route — code browser for objects-v1 snapshots + stored diff.

Snapshots use the ``objects-v1`` layout: per-file zstd-compressed objects
keyed by sha256 under ``<storage>/objects/<sha[:2]>/<sha>.zst``. The format
is detected via the ``format`` field in the metadata sibling
(``<id>.metadata.json``). Rows whose metadata is missing or whose ``format``
is not ``objects-v1`` are treated as legacy and rejected with HTTP 410;
``ailine prune-legacy-snapshots`` removes those rows and their orphan
sibling files.
"""

import json
import logging
import os
from typing import List, Optional, Tuple

from flask import Flask, render_template, request

from ailine.integrations.git_url import normalize_git_url
from ailine.persistence import repository
from ailine.snapshot import object_store
from ailine.snapshot.archive import SNAPSHOT_FORMAT_OBJECTS_V1
from ailine.snapshot.paths import ensure_utf8_text
from ailine.web.code_browser import (
    MAX_BLOB_BYTES,
    build_path_tree,
    detect_language,
    safe_relpath,
    truncate_text,
)
from ailine.web.diff_sections import split_unified_diff
from ailine.web.state import get_repo_url, load_repo_url


_LEGACY_SNAPSHOT_MESSAGE = (
    "Legacy snapshot format - run 'ailine prune-legacy-snapshots' to clean up"
)


def _metadata_path_for(manifest_path: Optional[str]) -> Optional[str]:
    if not manifest_path:
        return None
    if manifest_path.endswith(".manifest.json"):
        return manifest_path[: -len(".manifest.json")] + ".metadata.json"
    return None


def _load_metadata(manifest_path: Optional[str]) -> dict:
    meta_path = _metadata_path_for(manifest_path)
    if not meta_path or not os.path.exists(meta_path):
        return {}
    try:
        with open(meta_path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError) as exc:
        logging.warning("Could not parse %s: %s", meta_path, exc)
        return {}


def _load_manifest_entries(manifest_path: Optional[str]) -> List[dict]:
    if not manifest_path or not os.path.exists(manifest_path):
        return []
    with open(manifest_path, "r", encoding="utf-8") as f:
        return json.load(f) or []


def _included_paths(manifest: List[dict]) -> List[str]:
    paths: List[str] = []
    for entry in manifest:
        path = entry.get("path")
        decision = entry.get("decision")
        # Only show files that were actually included in the archive; pointer-only
        # or skipped large files have no extractable content.
        if not path or decision != "include":
            continue
        if entry.get("classification") not in (None, "include"):
            continue
        if any(part.startswith(".") for part in path.split("/")):
            continue
        paths.append(path)
    paths.sort()
    return paths


def _decode_blob_bytes(raw: bytes) -> Tuple[str, bool, bool]:
    """Decode ``raw`` to UTF-8 text, capping at ``MAX_BLOB_BYTES``.

    Returns ``(content, truncated, unreadable)``.
    """
    truncated = len(raw) > MAX_BLOB_BYTES
    capped = raw[:MAX_BLOB_BYTES] if truncated else raw
    try:
        text = capped.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return "", False, True
    return ensure_utf8_text(text), truncated, False


def _read_one_file_from_objects(
    manifest_entries: List[dict], storage_dir: str, rel_path: str
) -> Tuple[str, bool, bool]:
    """Read ``rel_path`` from the content-addressed object store."""
    sha = None
    for entry in manifest_entries:
        if entry.get("path") == rel_path and entry.get("decision") == "include":
            sha = entry.get("sha256")
            break
    if not sha:
        raise FileNotFoundError(rel_path)
    raw = object_store.read_object_bytes(sha, storage_dir, MAX_BLOB_BYTES + 1)
    return _decode_blob_bytes(raw)


def _load_diff_text(diff_path: str | None) -> Tuple[str | None, bool]:
    if not diff_path or not os.path.exists(diff_path):
        return None, False
    with open(diff_path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read(MAX_BLOB_BYTES + 1)
    truncated = len(text.encode("utf-8", errors="replace")) > MAX_BLOB_BYTES
    if truncated:
        text = text[:MAX_BLOB_BYTES]
    return text, truncated


def view(snapshot_id: str):
    load_repo_url()
    row = repository.fetch_snapshot_browser_row(snapshot_id)
    if not row:
        logging.warning(f"Snapshot {snapshot_id} not found")
        return "Snapshot not found", 404

    parent = row["parent"]
    manifest_path = row["manifest_path"]
    diff_path = row["diff_path"]

    metadata = _load_metadata(manifest_path)
    if metadata.get("format") != SNAPSHOT_FORMAT_OBJECTS_V1:
        logging.warning(
            "Snapshot %s is not in objects-v1 format (got %r)",
            snapshot_id,
            metadata.get("format"),
        )
        return _LEGACY_SNAPSHOT_MESSAGE, 410

    # Object store layout: <storage_dir>/objects/<sha[:2]>/<sha>.zst.
    # Metadata records the inner "objects/" directory; ``object_store`` APIs
    # take the *parent* (storage_dir) so we strip the trailing component.
    objects_dir = metadata.get("objects_dir")
    if objects_dir:
        storage_dir = os.path.dirname(os.path.abspath(objects_dir))
    else:
        storage_dir = os.path.dirname(os.path.abspath(manifest_path))

    manifest_entries = _load_manifest_entries(manifest_path)
    paths = _included_paths(manifest_entries)
    tree = build_path_tree(paths)
    view_mode = request.args.get("view", "files")
    if view_mode not in {"files", "diff"}:
        view_mode = "files"

    diff_payload = None
    if view_mode == "diff":
        diff_text, diff_truncated = _load_diff_text(diff_path)
        sections = []
        if diff_text and diff_text.strip():
            sections = [
                {"title": s.title, "body": s.body}
                for s in split_unified_diff(diff_text)
            ]
        diff_payload = {
            "text": diff_text,
            "sections": sections,
            "truncated": diff_truncated,
            "available": diff_text is not None and diff_text.strip() != "",
        }

    requested = request.args.get("path")
    selected = safe_relpath(requested, paths) if view_mode == "files" else None
    if requested and view_mode == "files" and not selected:
        return "File not found in snapshot", 404

    blob = None
    if selected:
        try:
            content, truncated, unreadable = _read_one_file_from_objects(
                manifest_entries, storage_dir, selected
            )
        except FileNotFoundError:
            return "File not found in snapshot", 404
        except Exception as e:
            logging.error(f"Failed to read {selected} from snapshot {snapshot_id}: {e}")
            return f"Failed to read file: {e}", 500
        if unreadable:
            blob = {
                "path": selected,
                "content": f"Binary or unreadable file: {selected}",
                "truncated": False,
                "language": "",
            }
        else:
            blob = {
                "path": selected,
                "content": content,
                "truncated": truncated,
                "language": detect_language(selected),
            }

    repo_url = row.get("git_url") or get_repo_url()
    parent_url = normalize_git_url(repo_url, parent) if parent and repo_url else None
    logging.info(f"Viewed snapshot {snapshot_id} (view={view_mode}, path={selected})")
    return render_template(
        "snapshot.html",
        snapshot_id=snapshot_id,
        parent=parent,
        parent_url=parent_url,
        tree=tree,
        paths=paths,
        selected_path=selected,
        blob=blob,
        view_mode=view_mode,
        diff=diff_payload,
        diff_available=bool(diff_path and os.path.exists(diff_path)),
    )


def register(app: Flask) -> None:
    app.add_url_rule("/snapshot/<snapshot_id>", endpoint="snapshot_view", view_func=view)
