"""Back-compat redirects: legacy ``/commits`` and ``/experiments`` map to ``/``.

Endpoint names ``commits`` and ``experiments`` are preserved so existing
``url_for(...)`` callers keep working.
"""

from flask import Flask, redirect, url_for


def commits_redirect():
    return redirect(url_for("lineage"), code=302)


def experiments_redirect():
    return redirect(url_for("lineage"), code=302)


def register(app: Flask) -> None:
    app.add_url_rule("/commits", endpoint="commits", view_func=commits_redirect)
    app.add_url_rule(
        "/experiments", endpoint="experiments", view_func=experiments_redirect
    )
