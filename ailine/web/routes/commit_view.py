"""``/commit/<id>`` route — read-only commit viewer using git object access."""

import logging

import git
from flask import Flask, render_template

from ailine.config import constants
from ailine.persistence import repository
from ailine.snapshot.paths import ensure_utf8_text
from ailine.web.state import load_repo_url


def view(commit_id: str):
    load_repo_url()
    git_url = repository.fetch_git_url(commit_id)
    if not git_url:
        logging.warning(f"Commit {commit_id} not found in database")
        return "Commit not found", 404

    try:
        repo = git.Repo(constants.REPO_DIR)
        full_commit_id = None
        for commit in repo.iter_commits():
            if commit.hexsha == commit_id or commit.hexsha.startswith(commit_id):
                full_commit_id = commit.hexsha
                break
        if not full_commit_id:
            logging.error(f"Commit {commit_id} not found in Git repository")
            return f"Commit {commit_id} not found in repository", 404

        logging.info(f"Commit view using read-only git object mode for {full_commit_id}")
        commit_files = repo.git.ls_tree("-r", full_commit_id, "--name-only").splitlines()
        logging.info(f"Files in commit {full_commit_id}: {commit_files}")
        files = []
        for rel_path in commit_files:
            path_parts = rel_path.split("/")
            if any(part.startswith(".") for part in path_parts):
                logging.info(f"Skipping hidden path in commit: {rel_path}")
                continue
            try:
                content = ensure_utf8_text(repo.git.show(f"{full_commit_id}:{rel_path}"))
                files.append({"path": rel_path, "content": content})
            except Exception as e:
                logging.warning(f"Unable to read commit object for {rel_path}: {str(e)}")
                files.append(
                    {"path": rel_path, "content": f"Binary or unreadable file: {rel_path}"}
                )

        logging.info(f"Found {len(files)} files for commit {full_commit_id}")
        if not files:
            logging.warning(f"No readable non-hidden files found for commit {full_commit_id}")
            files.append({"path": "N/A", "content": "No files found in this commit."})

        return render_template("commit.html", commit_id=commit_id, files=files, git_url=git_url)
    except Exception as e:
        logging.error(f"Error in commit_view for {commit_id}: {str(e)}")
        return f"Error processing commit {commit_id}: {str(e)}", 500


def register(app: Flask) -> None:
    app.add_url_rule("/commit/<commit_id>", endpoint="commit_view", view_func=view)
