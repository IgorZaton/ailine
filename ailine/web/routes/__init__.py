from flask import Flask

from ailine.web.routes import commit_view, commits, experiments, snapshot_view


def register_routes(app: Flask) -> None:
    commits.register(app)
    experiments.register(app)
    commit_view.register(app)
    snapshot_view.register(app)


__all__ = ["register_routes", "commits", "experiments", "commit_view", "snapshot_view"]
