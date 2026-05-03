"""``/commits`` route — list of git commits and snapshots known to ailine."""

import logging
import os

from flask import Flask, render_template

from ailine.config import constants
from ailine.persistence import repository
from ailine.web.state import load_repo_url


def view():
    load_repo_url()
    if not os.path.exists(constants.DB_PATH):
        return "Database not found. Run 'ailine init' and 'ailine run' first.", 500
    tree = repository.fetch_commits_overview()
    logging.info("Commits page accessed")
    return render_template("commits.html", tree=tree)


def register(app: Flask) -> None:
    app.add_url_rule("/commits", endpoint="commits", view_func=view)
