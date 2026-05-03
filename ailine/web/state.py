"""Lightweight session state shared between web routes and CLI."""

import logging
import os

from ailine.config import constants


_REPO_URL = None


def load_repo_url() -> str | None:
    global _REPO_URL
    if os.path.exists(constants.CONFIG_PATH):
        with open(constants.CONFIG_PATH, "r") as f:
            _REPO_URL = f.read().strip()
        logging.info(f"Loaded REPO_URL: {_REPO_URL}")
    return _REPO_URL


def set_repo_url(value: str | None) -> None:
    global _REPO_URL
    _REPO_URL = value


def get_repo_url() -> str | None:
    return _REPO_URL
