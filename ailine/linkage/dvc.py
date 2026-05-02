"""DVC linkage: discover outputs and classify cache/remote status."""

import os
import subprocess
from typing import Dict, List, Optional

import yaml

from ailine.snapshot.paths import is_excluded, normalize_rel_path


def discover_dvc_outputs(repo_path: str, dvc_cfg: dict) -> List[dict]:
    outputs: List[dict] = []

    for root, _, files in os.walk(repo_path):
        for filename in files:
            if not filename.endswith(".dvc"):
                continue
            dvc_file_path = os.path.join(root, filename)
            rel_dvc_file = normalize_rel_path(os.path.relpath(dvc_file_path, repo_path))
            if is_excluded(rel_dvc_file, dvc_cfg["ignore_paths"]):
                continue
            with open(dvc_file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            for out in data.get("outs", []):
                out_path = out.get("path")
                if not out_path:
                    continue
                rel_out = normalize_rel_path(
                    os.path.normpath(os.path.join(os.path.dirname(rel_dvc_file), out_path))
                )
                if is_excluded(rel_out, dvc_cfg["ignore_paths"]):
                    continue
                outputs.append({"path": rel_out, "out": out, "source": rel_dvc_file})

    dvc_yaml_path = os.path.join(repo_path, "dvc.yaml")
    if os.path.exists(dvc_yaml_path):
        with open(dvc_yaml_path, "r", encoding="utf-8") as f:
            dvc_yaml = yaml.safe_load(f) or {}
        for _stage_name, stage_cfg in (dvc_yaml.get("stages") or {}).items():
            for out in stage_cfg.get("outs", []):
                out_path = out if isinstance(out, str) else out.get("path")
                if not out_path:
                    continue
                rel_out = normalize_rel_path(os.path.normpath(out_path))
                if is_excluded(rel_out, dvc_cfg["ignore_paths"]):
                    continue
                out_dict = out if isinstance(out, dict) else {"path": out_path}
                outputs.append({"path": rel_out, "out": out_dict, "source": "dvc.yaml"})

    deduped: Dict[str, dict] = {}
    for item in outputs:
        deduped[item["path"]] = item
    return [deduped[path] for path in sorted(deduped)]


def get_dvc_remote_info(repo_path: str, remote_name: Optional[str]) -> dict:
    try:
        res = subprocess.run(
            ["dvc", "remote", "list"],
            cwd=repo_path,
            check=False,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {"has_remote": False, "remote_name": None, "probe_status": "dvc_not_installed"}
    if res.returncode != 0:
        return {"has_remote": False, "remote_name": None, "probe_status": "remote_list_failed"}
    lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    if not lines:
        return {"has_remote": False, "remote_name": None, "probe_status": "no_remote"}
    remotes = [line.split(maxsplit=1)[0] for line in lines]
    chosen = remote_name if remote_name in remotes else remotes[0]
    return {"has_remote": True, "remote_name": chosen, "probe_status": "remote_available"}


def build_dvc_linkage(repo_path: str, dvc_cfg: dict) -> dict:
    outputs = discover_dvc_outputs(repo_path, dvc_cfg)
    remote_info = get_dvc_remote_info(repo_path, dvc_cfg.get("remote_name"))
    linkage_items: List[dict] = []
    missing_hash = False
    all_in_cache = True
    any_in_cache = False

    for item in outputs:
        out = item["out"]
        hash_field = None
        hash_value = None
        for candidate in ("md5", "etag", "checksum", "hash"):
            if out.get(candidate):
                hash_field = candidate
                hash_value = out.get(candidate)
                break
        if not hash_value:
            missing_hash = True

        out_abs_path = os.path.join(repo_path, item["path"])
        in_cache = os.path.exists(out_abs_path)
        any_in_cache = any_in_cache or in_cache
        all_in_cache = all_in_cache and in_cache
        linkage_items.append(
            {
                "path": item["path"],
                "hash_algo": hash_field,
                "hash_value": hash_value,
                "size": out.get("size"),
                "nfiles": out.get("nfiles"),
                "is_in_cache": in_cache,
                "has_remote": remote_info["has_remote"],
                "remote_name": remote_info["remote_name"],
                "remote_probe_status": remote_info["probe_status"],
                "source": item["source"],
            }
        )

    if not linkage_items:
        status = "missing"
    elif dvc_cfg.get("require_hash_fields", True) and missing_hash:
        status = "partial"
    elif all_in_cache and remote_info["has_remote"]:
        status = "remote_ready"
    elif all_in_cache:
        status = "local_only"
    elif any_in_cache:
        status = "partial"
    else:
        status = "missing"

    return {"status": status, "items": linkage_items, "config": dvc_cfg}
