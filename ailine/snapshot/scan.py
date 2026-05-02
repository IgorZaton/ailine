"""Repository scan, large-file decision policy, and content-addressed pointers."""

import fnmatch
import json
import os
import shutil
from datetime import datetime
from typing import List, Tuple

import click
import yaml

from ailine.config import constants
from ailine.config.loader import (
    init_state_dirs,
    load_decision_store,
    save_decision_store,
)
from ailine.snapshot.paths import (
    is_excluded,
    normalize_rel_path,
    sha256_file,
)


def discover_dvc_tracked_paths(repo_path: str, dvc_patterns: List[str]) -> set:
    tracked = set()
    for root, _, files in os.walk(repo_path):
        for filename in files:
            rel_file = normalize_rel_path(os.path.relpath(os.path.join(root, filename), repo_path))
            if not any(fnmatch.fnmatch(rel_file, pat) for pat in dvc_patterns):
                continue
            try:
                with open(os.path.join(root, filename), "r", encoding="utf-8") as f:
                    content = yaml.safe_load(f) or {}
                for out in content.get("outs", []):
                    out_path = out.get("path")
                    if not out_path:
                        continue
                    tracked.add(
                        normalize_rel_path(
                            os.path.normpath(os.path.join(os.path.dirname(rel_file), out_path))
                        )
                    )
            except Exception:
                continue
    return tracked


def scan_repo_files(repo_path: str, policy: dict) -> List[dict]:
    large_limit = int(policy["large_file_mb"] * 1024 * 1024)
    dvc_tracked = discover_dvc_tracked_paths(repo_path, policy["dvc_pointer_patterns"])
    entries: List[dict] = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for filename in files:
            full_path = os.path.abspath(os.path.join(root, filename))
            rel_path = normalize_rel_path(os.path.relpath(full_path, repo_path))
            if is_excluded(rel_path, policy["exclude_globs"]):
                continue

            file_size = os.path.getsize(full_path)
            file_hash = sha256_file(full_path)
            if rel_path in dvc_tracked and file_size >= large_limit:
                classification = "large-and-dvc"
                decision = "pointer"
            elif file_size >= large_limit:
                classification = "large-non-dvc"
                decision = "pending"
            else:
                classification = "include"
                decision = "include"
            entries.append(
                {
                    "rel_path": rel_path,
                    "full_path": full_path,
                    "size": file_size,
                    "sha256": file_hash,
                    "classification": classification,
                    "decision": decision,
                    "decision_source": "policy" if decision != "pending" else None,
                }
            )
    entries.sort(key=lambda item: item["rel_path"])
    return entries


def resolve_large_file_decisions(entries: List[dict], policy: dict) -> Tuple[List[dict], dict]:
    store = load_decision_store()
    changed_store = False
    for entry in entries:
        if entry["classification"] != "large-non-dvc":
            continue
        key = f"{entry['rel_path']}::{entry['sha256']}"
        remembered = store["by_content"].get(key) or store["by_path"].get(entry["rel_path"])
        if remembered in {"include", "skip"}:
            entry["decision"] = remembered
            entry["decision_source"] = "memory"
            continue

        if policy.get("large_file_mode") != "prompt":
            entry["decision"] = "skip"
            entry["decision_source"] = "policy-default"
            continue

        choice = click.prompt(
            f"Large non-DVC file detected ({entry['rel_path']}, {entry['size']} bytes). "
            "Choose action",
            type=click.Choice(["include", "skip", "abort"], case_sensitive=False),
            default="skip",
            show_choices=True,
        ).lower()
        if choice == "abort":
            raise click.ClickException(f"Aborted due to large file: {entry['rel_path']}")

        remember = click.confirm(
            f"Remember decision '{choice}' for {entry['rel_path']}?", default=True
        )
        entry["decision"] = choice
        entry["decision_source"] = "prompt"
        if remember:
            store["by_content"][key] = choice
            store["by_path"][entry["rel_path"]] = choice
            changed_store = True
            entry["decision_source"] = "prompt+remembered"

    if changed_store:
        save_decision_store(store)
    return entries, store


def create_large_file_pointer(entry: dict, storage_dir: str, dvc_managed: bool) -> dict:
    init_state_dirs()
    content_hash = entry["sha256"]
    object_path = os.path.join(constants.OBJECT_STORE_DIR, content_hash)
    if not os.path.exists(object_path) and not dvc_managed:
        shutil.copy2(entry["full_path"], object_path)

    pointer_path = os.path.join(constants.POINTER_STORE_DIR, f"{content_hash}.dvc.json")
    pointer_payload = {
        "path": entry["rel_path"],
        "sha256": content_hash,
        "size": entry["size"],
        "object_path": object_path if not dvc_managed else None,
        "dvc_managed": dvc_managed,
        "storage_hint": storage_dir,
        "created_at": datetime.now().isoformat(),
    }
    with open(pointer_path, "w", encoding="utf-8") as f:
        json.dump(pointer_payload, f, indent=2, sort_keys=True)
    return {"pointer_path": pointer_path, "pointer_payload": pointer_payload}
