from flask import Flask

from ailine.web.routes import commit_view, lineage, redirects, snapshot_view


def register_routes(app: Flask) -> None:
    lineage.register(app)
    redirects.register(app)
    commit_view.register(app)
    snapshot_view.register(app)


__all__ = [
    "register_routes",
    "lineage",
    "redirects",
    "commit_view",
    "snapshot_view",
]
