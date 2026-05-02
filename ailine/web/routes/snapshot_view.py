"""``/snapshot/<id>`` route — extract a tar.zst archive into a temp dir and render."""

import logging
import os
import shutil

from flask import Flask, render_template

from ailine.integrations.git_url import normalize_git_url
from ailine.persistence import repository
from ailine.snapshot.archive import extract_tar_zst_archive
from ailine.web.state import get_repo_url, load_repo_url


def view(snapshot_id: str):
    load_repo_url()
    row = repository.fetch_snapshot_location(snapshot_id)
    if not row:
        logging.warning(f"Snapshot {snapshot_id} not found")
        return "Snapshot not found", 404

    snapshot_path, parent = row
    if not os.path.exists(snapshot_path):
        logging.error(f"Snapshot file not found at {snapshot_path}")
        return f"Snapshot file not found at {snapshot_path}", 500

    temp_dir = os.path.abspath(f"temp_{snapshot_id}")
    try:
        extract_tar_zst_archive(snapshot_path, temp_dir)
    except Exception as e:
        logging.error(f"Failed to unpack snapshot {snapshot_id}: {str(e)}")
        return f"Failed to unpack snapshot: {str(e)}", 500

    files = []
    for root, _dirs, filenames in os.walk(temp_dir):
        if any(part.startswith(".") for part in root.split(os.sep)):
            continue
        for filename in filenames:
            if filename.startswith("."):
                continue
            file_path = os.path.join(root, filename)
            rel_path = os.path.relpath(file_path, temp_dir)
            try:
                with open(file_path, "r", errors="ignore") as f:
                    content = f.read()
                files.append({"path": rel_path, "content": content})
            except Exception as e:
                files.append({"path": rel_path, "content": f"Error reading file: {str(e)}"})

    shutil.rmtree(temp_dir, ignore_errors=True)
    repo_url = get_repo_url()
    parent_url = normalize_git_url(repo_url, parent) if parent and repo_url else None
    logging.info(f"Viewed snapshot {snapshot_id}")
    return render_template(
        "snapshot.html", snapshot_id=snapshot_id, files=files, parent_url=parent_url
    )


def register(app: Flask) -> None:
    app.add_url_rule("/snapshot/<snapshot_id>", endpoint="snapshot_view", view_func=view)
