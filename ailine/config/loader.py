"""Loaders for the ``.ailine.yml`` policy file and on-disk decision state.

Each loader merges defaults with overrides from the policy file (if present)
and validates the result. Validation raises ``click.ClickException`` so user
errors are surfaced consistently from CLI entrypoints.
"""

import json
import os
from typing import Any, Dict

import click
import yaml

from ailine.config import constants
from ailine.config.defaults import (
    DEFAULT_DVC_CONFIG,
    DEFAULT_ENVIRONMENT_CONFIG,
    DEFAULT_RUN_CAPTURE_CONFIG,
    DEFAULT_SNAPSHOT_POLICY,
    REMOVED_DVC_KEYS,
)


def _read_policy_file() -> Dict[str, Any]:
    if not os.path.exists(constants.POLICY_PATH):
        return {}
    with open(constants.POLICY_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def init_state_dirs() -> None:
    os.makedirs(constants.STATE_DIR, exist_ok=True)
    os.makedirs(constants.OBJECT_STORE_DIR, exist_ok=True)
    os.makedirs(constants.POINTER_STORE_DIR, exist_ok=True)


def load_snapshot_policy() -> dict:
    policy = dict(DEFAULT_SNAPSHOT_POLICY)
    snapshot_cfg = _read_policy_file().get("snapshot", {})
    policy["exclude_globs"] = snapshot_cfg.get("exclude_globs", policy["exclude_globs"])
    policy["large_file_mb"] = snapshot_cfg.get("large_file_mb", policy["large_file_mb"])
    policy["large_file_mode"] = snapshot_cfg.get("large_file_mode", policy["large_file_mode"])
    policy["dvc_pointer_patterns"] = snapshot_cfg.get(
        "dvc_pointer_patterns", policy["dvc_pointer_patterns"]
    )
    return policy


def load_dvc_config() -> dict:
    cfg = dict(DEFAULT_DVC_CONFIG)
    dvc_cfg = _read_policy_file().get("dvc", {})

    removed = sorted(set(dvc_cfg) & REMOVED_DVC_KEYS.keys())
    if removed:
        details = ", ".join(f"dvc.{k} ({REMOVED_DVC_KEYS[k]})" for k in removed)
        raise click.ClickException(
            f"Removed config key(s) found in {constants.POLICY_PATH}: {details}. "
            "Delete these keys to continue."
        )

    for key in cfg:
        if key in dvc_cfg:
            cfg[key] = dvc_cfg[key]

    if not isinstance(cfg["require_hash_fields"], bool):
        raise click.ClickException(
            f"Invalid dvc.require_hash_fields '{cfg['require_hash_fields']}' in "
            f"{constants.POLICY_PATH}. Must be true/false."
        )
    if not isinstance(cfg["ignore_paths"], list):
        raise click.ClickException(
            f"Invalid dvc.ignore_paths in {constants.POLICY_PATH}. Must be a list."
        )
    return cfg


def load_environment_config() -> dict:
    cfg = dict(DEFAULT_ENVIRONMENT_CONFIG)
    env_cfg = _read_policy_file().get("environment", {})
    cfg["enabled"] = env_cfg.get("enabled", cfg["enabled"])
    cfg["packages"] = env_cfg.get("packages", cfg["packages"])

    if not isinstance(cfg["enabled"], bool):
        raise click.ClickException(
            f"Invalid environment.enabled '{cfg['enabled']}' in {constants.POLICY_PATH}. "
            "Must be true/false."
        )
    if not isinstance(cfg["packages"], list) or any(
        not isinstance(item, str) for item in cfg["packages"]
    ):
        raise click.ClickException(
            f"Invalid environment.packages in {constants.POLICY_PATH}. "
            "Must be a list of strings."
        )
    return cfg


def load_run_capture_config() -> dict:
    cfg = dict(DEFAULT_RUN_CAPTURE_CONFIG)
    run_capture_cfg = _read_policy_file().get("run_capture", {})
    cfg["enabled"] = run_capture_cfg.get("enabled", cfg["enabled"])
    if not isinstance(cfg["enabled"], bool):
        raise click.ClickException(
            f"Invalid run_capture.enabled '{cfg['enabled']}' in {constants.POLICY_PATH}. "
            "Must be true/false."
        )
    return cfg


def load_decision_store() -> dict:
    init_state_dirs()
    if os.path.exists(constants.LARGE_FILE_POLICY_STORE):
        with open(constants.LARGE_FILE_POLICY_STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"by_content": {}, "by_path": {}}


def save_decision_store(store: dict) -> None:
    init_state_dirs()
    with open(constants.LARGE_FILE_POLICY_STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)
