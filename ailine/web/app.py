"""Flask application factory and a module-level ``app`` instance for back-compat."""

import logging
import os

from flask import Flask

from ailine.config import constants


def create_app() -> Flask:
    flask_app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/static",
    )
    from ailine.web.routes import register_routes

    register_routes(flask_app)
    return flask_app


def _bootstrap_logging() -> None:
    if not logging.getLogger().handlers:
        log_dir = os.path.dirname(constants.LOG_PATH)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        logging.basicConfig(
            filename=constants.LOG_PATH,
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )


_bootstrap_logging()
app = create_app()
