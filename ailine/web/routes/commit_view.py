"""``/commit/<id>`` route — read-only commit browser using git object access."""

import logging

import git
from flask import Flask, render_template, request

from ailine.config import constants
from ailine.persistence import repository
from ailine.snapshot.paths import ensure_utf8_text
from ailine.web.code_browser import (
    build_path_tree,
    detect_language,
    safe_relpath,
    truncate_text,
)
from ailine.web.state import load_repo_url


def _resolve_full_commit(repo: git.Repo, commit_id: str) -> str | None:
    for commit in repo.iter_commits():
        if commit.hexsha == commit_id or commit.hexsha.startswith(commit_id):
            return commit.hexsha
    return None


def _list_visible_paths(repo: git.Repo, full_commit_id: str) -> list[str]:
    raw_paths = repo.git.ls_tree("-r", full_commit_id, "--name-only").splitlines()
    visible: list[str] = []
    for rel_path in raw_paths:
        if any(part.startswith(".") for part in rel_path.split("/")):
            continue
        visible.append(rel_path)
    visible.sort()
    return visible


def view(commit_id: str):
    load_repo_url()
    git_url = repository.fetch_git_url(commit_id)
    if not git_url:
        logging.warning(f"Commit {commit_id} not found in database")
        return "Commit not found", 404

    try:
        repo = git.Repo(constants.REPO_DIR)
        full_commit_id = _resolve_full_commit(repo, commit_id)
        if not full_commit_id:
            logging.error(f"Commit {commit_id} not found in Git repository")
            return f"Commit {commit_id} not found in repository", 404

        paths = _list_visible_paths(repo, full_commit_id)
        tree = build_path_tree(paths)
        requested = request.args.get("path")
        selected = safe_relpath(requested, paths)
        if requested and not selected:
            return "File not found in commit", 404

        blob = None
        if selected:
            try:
                content = ensure_utf8_text(repo.git.show(f"{full_commit_id}:{selected}"))
                content, truncated = truncate_text(content)
                blob = {
                    "path": selected,
                    "content": content,
                    "truncated": truncated,
                    "language": detect_language(selected),
                }
            except Exception as e:
                logging.warning(f"Unable to read commit object for {selected}: {str(e)}")
                blob = {
                    "path": selected,
                    "content": f"Binary or unreadable file: {selected}",
                    "truncated": False,
                    "language": "",
                }

        return render_template(
            "commit.html",
            commit_id=commit_id,
            git_url=git_url,
            tree=tree,
            paths=paths,
            selected_path=selected,
            blob=blob,
        )
    except Exception as e:
        logging.error(f"Error in commit_view for {commit_id}: {str(e)}")
        return f"Error processing commit {commit_id}: {str(e)}", 500


def register(app: Flask) -> None:
    app.add_url_rule("/commit/<commit_id>", endpoint="commit_view", view_func=view)
