"""Single validation surface for ``.ailine.yml``.

This module is the **only** place that knows the full schema of the policy
file. Both ``ailine doctor`` and ``ailine track`` go through
:func:`validate_config` so the two never drift apart.

Backward-compat: existing per-section ``load_*`` helpers in
:mod:`ailine.config.loader` keep working for code paths that have not yet been
ported. They call into the same readers used here.
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import click
import yaml

from ailine.config import constants
from ailine.config.defaults import (
    DEFAULT_CLEANUP_CONFIG,
    DEFAULT_DVC_CONFIG,
    DEFAULT_ENVIRONMENT_CONFIG,
    DEFAULT_PROJECT_CONFIG,
    DEFAULT_RUN_CAPTURE_CONFIG,
    DEFAULT_SNAPSHOT_POLICY,
    DEFAULT_TRACK_CONFIG,
    REMOVED_DVC_KEYS,
    SUPPORTED_PROJECT_VERSIONS,
    VALID_DVC_VERIFY_LEVELS,
    VALID_MLFLOW_INHERIT_NAME_SYNC,
    VALID_MLFLOW_LINK_STRATEGIES,
    VALID_MLFLOW_MODES,
    VALID_PROJECT_MODES,
)


KNOWN_TOP_LEVEL_KEYS = {
    "project",
    "track",
    "snapshot",
    "dvc",
    "environment",
    "run_capture",
    "cleanup",
}


@dataclass
class ValidatedConfig:
    """Typed bundle of resolved config sections."""

    config_path: str
    config_exists: bool
    project: Dict[str, Any]
    track: Dict[str, Any]
    snapshot: Dict[str, Any]
    dvc: Dict[str, Any]
    environment: Dict[str, Any]
    run_capture: Dict[str, Any]
    cleanup: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)


class ConfigValidationError(click.ClickException):
    """Raised when the policy file fails schema validation."""


def _read_raw(config_path: str) -> tuple[Dict[str, Any], bool]:
    if not os.path.exists(config_path):
        return {}, False
    with open(config_path, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    if not isinstance(loaded, dict):
        raise ConfigValidationError(
            f"{config_path} must contain a YAML mapping at the top level."
        )
    return loaded, True


def _merge_defaults(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-copy defaults then overlay user values (one nested dict level supported).

    Deep copy is critical: callers (and tests) mutate the returned bundle, and
    we MUST NOT leak those mutations back into the module-level DEFAULT_*
    dicts.
    """
    merged: Dict[str, Any] = copy.deepcopy(base)
    for key, user_value in override.items():
        if key not in merged:
            continue  # unknown nested keys are dropped silently here; warned at top level
        default_value = base.get(key)
        if isinstance(default_value, dict) and isinstance(user_value, dict):
            merged[key] = {**copy.deepcopy(default_value), **copy.deepcopy(user_value)}
        else:
            merged[key] = copy.deepcopy(user_value)
    return merged


def _validate_project(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _merge_defaults(DEFAULT_PROJECT_CONFIG, raw)
    if cfg["version"] not in SUPPORTED_PROJECT_VERSIONS:
        raise ConfigValidationError(
            f"Unsupported project.version '{cfg['version']}'. "
            f"Supported: {sorted(SUPPORTED_PROJECT_VERSIONS)}."
        )
    if cfg["mode"] not in VALID_PROJECT_MODES:
        raise ConfigValidationError(
            f"Invalid project.mode '{cfg['mode']}'. Allowed: {sorted(VALID_PROJECT_MODES)}."
        )
    return cfg


def _validate_track(raw: Dict[str, Any], warnings: List[str]) -> Dict[str, Any]:
    cfg = _merge_defaults(DEFAULT_TRACK_CONFIG, raw)

    if cfg["repo_root"] != "auto" and not isinstance(cfg["repo_root"], str):
        raise ConfigValidationError(
            "track.repo_root must be 'auto' or a string path."
        )

    mlflow_cfg = cfg["mlflow"]
    if mlflow_cfg["mode"] not in VALID_MLFLOW_MODES:
        raise ConfigValidationError(
            f"Invalid track.mlflow.mode '{mlflow_cfg['mode']}'. "
            f"Allowed: {sorted(VALID_MLFLOW_MODES)}."
        )
    if not isinstance(mlflow_cfg["set_env"], bool):
        raise ConfigValidationError(
            "track.mlflow.set_env must be true/false."
        )
    if mlflow_cfg["inherit_name_sync"] not in VALID_MLFLOW_INHERIT_NAME_SYNC:
        raise ConfigValidationError(
            f"Invalid track.mlflow.inherit_name_sync '{mlflow_cfg['inherit_name_sync']}'. "
            f"Allowed: {sorted(VALID_MLFLOW_INHERIT_NAME_SYNC)}."
        )

    # Migrate legacy `prelink: bool` -> `link_strategy`. Only honor it when the
    # user has not also set `link_strategy` explicitly (explicit wins).
    raw_mlflow = raw.get("mlflow") if isinstance(raw, dict) else None
    raw_mlflow = raw_mlflow if isinstance(raw_mlflow, dict) else {}
    legacy_prelink = raw_mlflow.get("prelink", None)
    explicit_strategy = "link_strategy" in raw_mlflow
    if legacy_prelink is not None:
        if not isinstance(legacy_prelink, bool):
            raise ConfigValidationError(
                "track.mlflow.prelink must be true/false."
            )
        if not explicit_strategy:
            migrated = "prelink" if legacy_prelink else "none"
            mlflow_cfg["link_strategy"] = migrated
            warnings.append(
                "track.mlflow.prelink is deprecated; migrated to "
                f"track.mlflow.link_strategy='{migrated}'. "
                "Replace 'prelink' with 'link_strategy' in .ailine.yml."
            )
        else:
            warnings.append(
                "track.mlflow.prelink is deprecated and ignored because "
                "track.mlflow.link_strategy is set explicitly. "
                "Remove 'prelink' from .ailine.yml."
            )
    # Legacy default `prelink` may have been left in DEFAULT_TRACK_CONFIG copies
    # via tests; drop it before downstream code reads the merged dict.
    mlflow_cfg.pop("prelink", None)

    if mlflow_cfg["link_strategy"] not in VALID_MLFLOW_LINK_STRATEGIES:
        raise ConfigValidationError(
            f"Invalid track.mlflow.link_strategy '{mlflow_cfg['link_strategy']}'. "
            f"Allowed: {sorted(VALID_MLFLOW_LINK_STRATEGIES)}."
        )
    poll = mlflow_cfg["link_poll_seconds"]
    if not isinstance(poll, (int, float)) or isinstance(poll, bool) or poll <= 0:
        raise ConfigValidationError(
            "track.mlflow.link_poll_seconds must be a positive number."
        )

    dvc_cfg = cfg["dvc"]
    if dvc_cfg["verify"] not in VALID_DVC_VERIFY_LEVELS:
        raise ConfigValidationError(
            f"Invalid track.dvc.verify '{dvc_cfg['verify']}'. "
            f"Allowed: {sorted(VALID_DVC_VERIFY_LEVELS)}."
        )
    verify_commands = dvc_cfg["verify_commands"]
    if not isinstance(verify_commands, list) or not all(
        isinstance(cmd, list) and all(isinstance(arg, str) for arg in cmd)
        for cmd in verify_commands
    ):
        raise ConfigValidationError(
            "track.dvc.verify_commands must be a list of argv lists, e.g. "
            "[['dvc', 'status', '--quiet']]."
        )

    return cfg


def _validate_snapshot(raw: Dict[str, Any]) -> Dict[str, Any]:
    if "exclude_globs" in raw:
        raise ConfigValidationError(
            "snapshot.exclude_globs is no longer supported. Move your patterns "
            "into .ailineignore (gitignore syntax). See docs/track-contract.md."
        )
    cfg = _merge_defaults(DEFAULT_SNAPSHOT_POLICY, raw)
    if not isinstance(cfg["large_file_mb"], (int, float)) or cfg["large_file_mb"] <= 0:
        raise ConfigValidationError("snapshot.large_file_mb must be a positive number.")
    if cfg["large_file_mode"] not in {"prompt", "skip", "include"}:
        raise ConfigValidationError(
            f"Invalid snapshot.large_file_mode '{cfg['large_file_mode']}'. "
            "Allowed: ['include', 'prompt', 'skip']."
        )
    if not isinstance(cfg["dvc_pointer_patterns"], list) or not all(
        isinstance(p, str) for p in cfg["dvc_pointer_patterns"]
    ):
        raise ConfigValidationError(
            "snapshot.dvc_pointer_patterns must be a list of glob strings."
        )
    storage_dir = cfg.get("storage_dir")
    if storage_dir is not None and not isinstance(storage_dir, str):
        raise ConfigValidationError(
            "snapshot.storage_dir must be a string path or null."
        )
    return cfg


def _validate_dvc(raw: Dict[str, Any]) -> Dict[str, Any]:
    removed = sorted(set(raw) & REMOVED_DVC_KEYS.keys())
    if removed:
        details = ", ".join(f"dvc.{k} ({REMOVED_DVC_KEYS[k]})" for k in removed)
        raise ConfigValidationError(
            f"Removed config key(s) found: {details}. Delete these keys to continue."
        )

    cfg = _merge_defaults(DEFAULT_DVC_CONFIG, raw)
    if not isinstance(cfg["require_hash_fields"], bool):
        raise ConfigValidationError(
            f"Invalid dvc.require_hash_fields '{cfg['require_hash_fields']}'. "
            "Must be true/false."
        )
    if not isinstance(cfg["ignore_paths"], list):
        raise ConfigValidationError("dvc.ignore_paths must be a list.")
    if cfg["remote_name"] is not None and not isinstance(cfg["remote_name"], str):
        raise ConfigValidationError("dvc.remote_name must be null or a string.")
    return cfg


def _validate_environment(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _merge_defaults(DEFAULT_ENVIRONMENT_CONFIG, raw)
    if not isinstance(cfg["enabled"], bool):
        raise ConfigValidationError(
            f"Invalid environment.enabled '{cfg['enabled']}'. Must be true/false."
        )
    if not isinstance(cfg["packages"], list) or any(
        not isinstance(item, str) for item in cfg["packages"]
    ):
        raise ConfigValidationError(
            "environment.packages must be a list of strings."
        )
    return cfg


def _validate_run_capture(raw: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _merge_defaults(DEFAULT_RUN_CAPTURE_CONFIG, raw)
    if not isinstance(cfg["enabled"], bool):
        raise ConfigValidationError(
            f"Invalid run_capture.enabled '{cfg['enabled']}'. Must be true/false."
        )
    return cfg


def _validate_cleanup(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise ConfigValidationError("cleanup must be a YAML mapping.")
    cfg = _merge_defaults(DEFAULT_CLEANUP_CONFIG, raw)
    remove_cfg = cfg.get("remove")
    if not isinstance(remove_cfg, dict):
        raise ConfigValidationError("cleanup.remove must be a YAML mapping.")
    if not isinstance(remove_cfg.get("with_mlflow", False), bool):
        raise ConfigValidationError(
            "cleanup.remove.with_mlflow must be true/false."
        )
    return cfg


def validate_config(config_path: Optional[str] = None) -> ValidatedConfig:
    """Parse, schema-check, and merge defaults for ``.ailine.yml``.

    Parameters
    ----------
    config_path:
        Override the default :data:`constants.POLICY_PATH`. When the file does
        not exist, defaults are returned and ``config_exists=False`` is set so
        callers (typically ``ailine doctor``) can decide whether that is fatal.
    """
    path = config_path or constants.POLICY_PATH
    raw, exists = _read_raw(path)

    warnings: List[str] = []
    unknown = sorted(set(raw) - KNOWN_TOP_LEVEL_KEYS)
    if unknown:
        warnings.append(
            f"Unknown top-level key(s) in {path}: {unknown}. They will be ignored."
        )

    return ValidatedConfig(
        config_path=path,
        config_exists=exists,
        project=_validate_project(raw.get("project") or {}),
        track=_validate_track(raw.get("track") or {}, warnings),
        snapshot=_validate_snapshot(raw.get("snapshot") or {}),
        dvc=_validate_dvc(raw.get("dvc") or {}),
        environment=_validate_environment(raw.get("environment") or {}),
        run_capture=_validate_run_capture(raw.get("run_capture") or {}),
        cleanup=_validate_cleanup(raw.get("cleanup") or {}),
        warnings=warnings,
    )
