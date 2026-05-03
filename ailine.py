from enum import Enum
import fnmatch
import hashlib
import importlib.metadata
import json
import logging
import os
import platform
import sqlite3
import subprocess
import shutil
import sys
import tarfile
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import atexit
import click
import git
import mlflow
import yaml
import zstandard as zstd
from flask import Flask, render_template

from utils import print_formatted_data, print_table

DB_PATH = os.environ.get("AILINE_DB_PATH", "ailine_tree.db")
CONFIG_PATH = os.environ.get("AILINE_CONFIG_PATH", "ailine_config.txt")
REPO_DIR = os.environ.get("AILINE_REPO_DIR", "repo")
MLFLOW_TRACKING_URI = os.environ.get("AILINE_MLFLOW_URI", "http://localhost:5001")
MLFLOW_STORAGE_DIR = os.environ.get("AILINE_MLFLOW_STORAGE", os.path.abspath("mlruns"))
LOG_PATH = os.environ.get("AILINE_LOG_PATH", "ailine.log")
DEFAULT_STORAGE_DIR = os.environ.get("AILINE_STORAGE_DIR", os.path.abspath("snapshots"))
POLICY_PATH = os.environ.get("AILINE_POLICY_PATH", ".ailine.yml")
STATE_DIR = os.environ.get("AILINE_STATE_DIR", ".ailine")
LARGE_FILE_POLICY_STORE = os.path.join(STATE_DIR, "large-file-policy.json")
OBJECT_STORE_DIR = os.path.join(STATE_DIR, "objects")
POINTER_STORE_DIR = os.path.join(STATE_DIR, "pointers")

logging.basicConfig(filename=LOG_PATH, level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
app = Flask(__name__)
REPO_URL = None
MLFLOW_PROCESS = None

DEFAULT_SNAPSHOT_POLICY = {
    "exclude_globs": [
        ".git/**",
        ".dvc/cache/**",
        ".venv/**",
        "__pycache__/**",
        "*.pyc",
        "*.pyo",
        ".pytest_cache/**",
        ".mypy_cache/**",
        "mlruns/**",
        ".ailine/**",
    ],
    "large_file_mb": 50,
    "large_file_mode": "prompt",
    "dvc_pointer_patterns": ["*.dvc"],
}
DEFAULT_DVC_CONFIG = {
    "mode": "local_or_remote",
    "scope": "all_dvc_tracked",
    "remote_name": None,
    "auto_pull_missing": True,
    "require_hash_fields": True,
    "status_verbose_limit": 10,
    "ignore_paths": [],
}
VALID_DVC_MODES = {"local_or_remote"}
VALID_DVC_SCOPES = {"all_dvc_tracked"}
DEFAULT_ENVIRONMENT_CONFIG = {
    "enabled": True,
    "packages": ["mlflow", "flask", "gitpython", "dvc"],
}
DEFAULT_RUN_CAPTURE_CONFIG = {
    "enabled": True,
}

class CommitType(str, Enum):
    GIT = "git"
    SNAPSHOT = "snapshot"


def init_state_dirs():
    os.makedirs(STATE_DIR, exist_ok=True)
    os.makedirs(OBJECT_STORE_DIR, exist_ok=True)
    os.makedirs(POINTER_STORE_DIR, exist_ok=True)


def load_snapshot_policy() -> dict:
    policy = dict(DEFAULT_SNAPSHOT_POLICY)
    if os.path.exists(POLICY_PATH):
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        snapshot_cfg = loaded.get("snapshot", {})
        policy["exclude_globs"] = snapshot_cfg.get("exclude_globs", policy["exclude_globs"])
        policy["large_file_mb"] = snapshot_cfg.get("large_file_mb", policy["large_file_mb"])
        policy["large_file_mode"] = snapshot_cfg.get("large_file_mode", policy["large_file_mode"])
        policy["dvc_pointer_patterns"] = snapshot_cfg.get("dvc_pointer_patterns", policy["dvc_pointer_patterns"])
    return policy


def load_dvc_config() -> dict:
    cfg = dict(DEFAULT_DVC_CONFIG)
    if os.path.exists(POLICY_PATH):
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        dvc_cfg = loaded.get("dvc", {})
        cfg["mode"] = dvc_cfg.get("mode", cfg["mode"])
        cfg["scope"] = dvc_cfg.get("scope", cfg["scope"])
        cfg["remote_name"] = dvc_cfg.get("remote_name", cfg["remote_name"])
        cfg["auto_pull_missing"] = dvc_cfg.get("auto_pull_missing", cfg["auto_pull_missing"])
        cfg["require_hash_fields"] = dvc_cfg.get("require_hash_fields", cfg["require_hash_fields"])
        cfg["status_verbose_limit"] = dvc_cfg.get("status_verbose_limit", cfg["status_verbose_limit"])
        cfg["ignore_paths"] = dvc_cfg.get("ignore_paths", cfg["ignore_paths"])

    if cfg["mode"] not in VALID_DVC_MODES:
        raise click.ClickException(f"Invalid dvc.mode '{cfg['mode']}' in {POLICY_PATH}. Allowed: {sorted(VALID_DVC_MODES)}")
    if cfg["scope"] not in VALID_DVC_SCOPES:
        raise click.ClickException(f"Invalid dvc.scope '{cfg['scope']}' in {POLICY_PATH}. Allowed: {sorted(VALID_DVC_SCOPES)}")
    if not isinstance(cfg["status_verbose_limit"], int) or cfg["status_verbose_limit"] < 1:
        raise click.ClickException(f"Invalid dvc.status_verbose_limit '{cfg['status_verbose_limit']}' in {POLICY_PATH}. Must be integer >= 1.")
    if not isinstance(cfg["ignore_paths"], list):
        raise click.ClickException(f"Invalid dvc.ignore_paths in {POLICY_PATH}. Must be a list.")
    return cfg


def load_environment_config() -> dict:
    cfg = dict(DEFAULT_ENVIRONMENT_CONFIG)
    if os.path.exists(POLICY_PATH):
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        env_cfg = loaded.get("environment", {})
        cfg["enabled"] = env_cfg.get("enabled", cfg["enabled"])
        cfg["packages"] = env_cfg.get("packages", cfg["packages"])

    if not isinstance(cfg["enabled"], bool):
        raise click.ClickException(f"Invalid environment.enabled '{cfg['enabled']}' in {POLICY_PATH}. Must be true/false.")
    if not isinstance(cfg["packages"], list) or any(not isinstance(item, str) for item in cfg["packages"]):
        raise click.ClickException(f"Invalid environment.packages in {POLICY_PATH}. Must be a list of strings.")
    return cfg


def load_run_capture_config() -> dict:
    cfg = dict(DEFAULT_RUN_CAPTURE_CONFIG)
    if os.path.exists(POLICY_PATH):
        with open(POLICY_PATH, "r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f) or {}
        run_capture_cfg = loaded.get("run_capture", {})
        cfg["enabled"] = run_capture_cfg.get("enabled", cfg["enabled"])
    if not isinstance(cfg["enabled"], bool):
        raise click.ClickException(f"Invalid run_capture.enabled '{cfg['enabled']}' in {POLICY_PATH}. Must be true/false.")
    return cfg


def load_decision_store() -> dict:
    init_state_dirs()
    if os.path.exists(LARGE_FILE_POLICY_STORE):
        with open(LARGE_FILE_POLICY_STORE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"by_content": {}, "by_path": {}}


def save_decision_store(store: dict):
    init_state_dirs()
    with open(LARGE_FILE_POLICY_STORE, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=2, sort_keys=True)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def normalize_rel_path(path: str) -> str:
    return path.replace(os.sep, "/")


def is_excluded(rel_path: str, exclude_globs: List[str]) -> bool:
    norm = normalize_rel_path(rel_path)
    for pattern in exclude_globs:
        if fnmatch.fnmatch(norm, pattern):
            return True
    return False


def discover_dvc_tracked_paths(repo_path: str, dvc_patterns: List[str]) -> set:
    tracked = set()
    for root, _, files in os.walk(repo_path):
        for filename in files:
            rel_file = normalize_rel_path(os.path.relpath(os.path.join(root, filename), repo_path))
            if any(fnmatch.fnmatch(rel_file, pat) for pat in dvc_patterns):
                try:
                    with open(os.path.join(root, filename), "r", encoding="utf-8") as f:
                        content = yaml.safe_load(f) or {}
                    outs = content.get("outs", [])
                    for out in outs:
                        out_path = out.get("path")
                        if not out_path:
                            continue
                        tracked.add(normalize_rel_path(os.path.normpath(os.path.join(os.path.dirname(rel_file), out_path))))
                except Exception:
                    continue
    return tracked


def scan_repo_files(repo_path: str, policy: dict) -> List[dict]:
    large_limit = int(policy["large_file_mb"] * 1024 * 1024)
    dvc_tracked = discover_dvc_tracked_paths(repo_path, policy["dvc_pointer_patterns"])
    entries = []
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d != ".git"]
        for filename in files:
            full_path = os.path.abspath(os.path.join(root, filename))
            rel_path = normalize_rel_path(os.path.relpath(full_path, repo_path))
            if is_excluded(rel_path, policy["exclude_globs"]):
                # Excluded paths are fully ignored and do not appear in manifests.
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
            f"Large non-DVC file detected ({entry['rel_path']}, {entry['size']} bytes). Choose action",
            type=click.Choice(["include", "skip", "abort"], case_sensitive=False),
            default="skip",
            show_choices=True,
        ).lower()
        if choice == "abort":
            raise click.ClickException(f"Aborted due to large file: {entry['rel_path']}")

        remember = click.confirm(f"Remember decision '{choice}' for {entry['rel_path']}?", default=True)
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
    object_path = os.path.join(OBJECT_STORE_DIR, content_hash)
    if not os.path.exists(object_path) and not dvc_managed:
        shutil.copy2(entry["full_path"], object_path)

    pointer_path = os.path.join(POINTER_STORE_DIR, f"{content_hash}.dvc.json")
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


def build_manifest(entries: List[dict], storage_dir: str) -> Tuple[List[dict], List[dict], dict]:
    manifest_entries = []
    archive_entries = []
    pointers = []
    for entry in entries:
        current = {
            "path": entry["rel_path"],
            "size": entry["size"],
            "sha256": entry["sha256"],
            "classification": entry["classification"],
            "decision": entry["decision"],
            "decision_source": entry["decision_source"],
        }
        if entry["classification"] == "excluded-by-policy":
            current["reason"] = "excluded-by-policy"
        elif entry["classification"] == "large-and-dvc":
            pointer = create_large_file_pointer(entry, storage_dir, dvc_managed=True)
            current["pointer"] = pointer["pointer_path"]
            pointers.append(pointer["pointer_payload"])
        elif entry["classification"] == "large-non-dvc":
            if entry["decision"] == "include":
                pointer = create_large_file_pointer(entry, storage_dir, dvc_managed=False)
                current["pointer"] = pointer["pointer_path"]
                pointers.append(pointer["pointer_payload"])
            elif entry["decision"] == "skip":
                current["reason"] = "user-skip"
            else:
                current["reason"] = "unknown"
        else:
            archive_entries.append(entry)
        manifest_entries.append(current)

    manifest_json = json.dumps(manifest_entries, sort_keys=True, separators=(",", ":"))
    snapshot_id = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    manifest_summary = {
        "snapshot_id": snapshot_id,
        "included_file_count": len(archive_entries),
        "excluded_file_count": len([m for m in manifest_entries if m["decision"] != "include"]),
        "large_file_pointer_count": len(pointers),
        "included_bytes": sum(item["size"] for item in archive_entries),
        "excluded_bytes": sum(item["size"] for item in entries if item["decision"] != "include"),
    }
    return manifest_entries, archive_entries, {"summary": manifest_summary, "pointers": pointers}


def discover_dvc_outputs(repo_path: str, dvc_cfg: dict) -> List[dict]:
    outputs: List[dict] = []

    # Parse all standalone .dvc files
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
                rel_out = normalize_rel_path(os.path.normpath(os.path.join(os.path.dirname(rel_dvc_file), out_path)))
                if is_excluded(rel_out, dvc_cfg["ignore_paths"]):
                    continue
                outputs.append({"path": rel_out, "out": out, "source": rel_dvc_file})

    # Parse root dvc.yaml stages if present
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
        res = subprocess.run(["dvc", "remote", "list"], cwd=repo_path, check=False, capture_output=True, text=True)
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
    linkage_items = []
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

    return {
        "status": status,
        "items": linkage_items,
        "config": dvc_cfg,
    }


def materialize_dvc_linkage(repo_path: str, dvc_linkage_json: str, dvc_cfg: dict) -> dict:
    payload = json.loads(dvc_linkage_json) if dvc_linkage_json else {}
    items = payload.get("items", [])
    unresolved = [item["path"] for item in items if not item.get("is_in_cache")]
    pulled = False

    if unresolved and dvc_cfg.get("auto_pull_missing", True):
        cmd = ["dvc", "pull"]
        if dvc_cfg.get("remote_name"):
            cmd += ["-r", dvc_cfg["remote_name"]]
        result = subprocess.run(cmd, cwd=repo_path, check=False, capture_output=True, text=True)
        pulled = result.returncode == 0
        unresolved = [path for path in unresolved if not os.path.exists(os.path.join(repo_path, path))]

    return {"pulled": pulled, "unresolved": unresolved, "total": len(items)}


def collect_environment_fingerprint(repo_root: str, env_cfg: dict) -> Tuple[dict, str]:
    if not env_cfg.get("enabled", True):
        return {"enabled": False}, "missing"

    status = "complete"
    poetry_lock_path = os.path.join(repo_root, "poetry.lock")
    poetry_lock_sha256 = None
    if os.path.exists(poetry_lock_path):
        poetry_lock_sha256 = sha256_file(poetry_lock_path)
    else:
        status = "partial"

    package_versions = {}
    for package in env_cfg.get("packages", []):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = None
            status = "partial"

    fingerprint = {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "poetry_lock_sha256": poetry_lock_sha256,
        "packages": package_versions,
        "captured_at": datetime.now().isoformat(),
    }
    return fingerprint, status


def build_run_command_payload(script: str, dataset: str, storage: str, repo_cwd: str) -> Tuple[dict, str]:
    resolved_command = f"python {script}"
    payload = {
        "entrypoint": "python",
        "script": script,
        "dataset": dataset,
        "storage": storage,
        "resolved_command": resolved_command,
        "cwd": repo_cwd,
        "captured_at": datetime.now().isoformat(),
    }
    summary = f"{resolved_command} --dataset {dataset} --storage {storage}"
    return payload, summary


def ensure_utf8_text(content: str) -> str:
    # Normalize potentially invalid surrogate-containing strings from git output.
    return content.encode("utf-8", errors="replace").decode("utf-8")

def start_mlflow_ui():
    global MLFLOW_PROCESS
    if MLFLOW_PROCESS is None:
        cmd = [
            "mlflow", "ui",
            "--backend-store-uri", MLFLOW_STORAGE_DIR,
            "--host", "0.0.0.0",
            "--port", "5001"
        ]
        try:
            MLFLOW_PROCESS = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            logging.info(f"Started MLflow UI on port 5001 with PID {MLFLOW_PROCESS.pid}")
            time.sleep(2)
            atexit.register(cleanup_mlflow_ui)
        except Exception as e:
            logging.error(f"Failed to start MLflow UI: {str(e)}")
            raise

def cleanup_mlflow_ui():
    global MLFLOW_PROCESS
    if MLFLOW_PROCESS is not None:
        MLFLOW_PROCESS.terminate()
        MLFLOW_PROCESS.wait()
        logging.info(f"Terminated MLflow UI with PID {MLFLOW_PROCESS.pid}")
        MLFLOW_PROCESS = None

def init_db():
    if os.path.exists(DB_PATH):
        logging.info(f"Database found at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS tree (
        id TEXT PRIMARY KEY,
        type TEXT,
        parent TEXT,
        mlflow_run TEXT,
        dvc_version TEXT,
        snapshot_path TEXT,
        timestamp TEXT,
        git_url TEXT,
        manifest_path TEXT,
        metadata_path TEXT,
        archive_bytes INTEGER,
        included_file_count INTEGER,
        excluded_file_count INTEGER,
        large_file_pointer_count INTEGER,
        diff_path TEXT,
        dvc_linkage_json TEXT,
        dvc_linkage_status TEXT,
        env_fingerprint_json TEXT,
        env_fingerprint_status TEXT,
        run_command_json TEXT,
        run_command_summary TEXT
    )''')
    # migration-safe additive columns for existing DBs
    c.execute("PRAGMA table_info(tree)")
    existing_columns = {row[1] for row in c.fetchall()}
    additive_columns = {
        "manifest_path": "TEXT",
        "metadata_path": "TEXT",
        "archive_bytes": "INTEGER",
        "included_file_count": "INTEGER",
        "excluded_file_count": "INTEGER",
        "large_file_pointer_count": "INTEGER",
        "diff_path": "TEXT",
        "dvc_linkage_json": "TEXT",
        "dvc_linkage_status": "TEXT",
        "env_fingerprint_json": "TEXT",
        "env_fingerprint_status": "TEXT",
        "run_command_json": "TEXT",
        "run_command_summary": "TEXT",
    }
    for name, col_type in additive_columns.items():
        if name not in existing_columns:
            c.execute(f"ALTER TABLE tree ADD COLUMN {name} {col_type}")
    conn.commit()
    conn.close()
    logging.info("Database initialized")

def create_snapshot_metafile(snapshot_hash: str, parent_commit_hash: str):
    data = {
        "parent_commit_hash": parent_commit_hash,
        "hash": snapshot_hash,
    }
    with open(".meta.yaml", "w", encoding="utf-8") as meta_file:
        yaml.dump(data, meta_file, default_flow_style=False)


def create_tar_zst_archive(snapshot_base: str, repo_path: str, archive_entries: List[dict]) -> str:
    archive_path = f"{snapshot_base}.tar.zst"
    with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp_tar:
        temp_tar_path = tmp_tar.name
    try:
        with tarfile.open(temp_tar_path, mode="w") as tar:
            for entry in sorted(archive_entries, key=lambda item: item["rel_path"]):
                tar.add(entry["full_path"], arcname=entry["rel_path"], recursive=False)
        cctx = zstd.ZstdCompressor(level=10)
        with open(temp_tar_path, "rb") as src, open(archive_path, "wb") as dst:
            cctx.copy_stream(src, dst)
    finally:
        if os.path.exists(temp_tar_path):
            os.remove(temp_tar_path)
    return archive_path


def extract_tar_zst_archive(archive_path: str, output_dir: str):
    dctx = zstd.ZstdDecompressor()
    with open(archive_path, "rb") as src:
        with dctx.stream_reader(src) as reader:
            with tarfile.open(fileobj=reader, mode="r|") as tar:
                tar.extractall(output_dir)


def create_snapshot(manifest_entries: List[dict], archive_entries: List[dict], parent_commit_hash: str, storage_dir: str, diff_text: str, untracked_files: List[str]):
    manifest_json = json.dumps(manifest_entries, sort_keys=True, separators=(",", ":"))
    snapshot_hash = hashlib.sha256(manifest_json.encode("utf-8")).hexdigest()
    snapshot_dir = os.path.abspath(storage_dir)
    snapshot_base = os.path.join(snapshot_dir, snapshot_hash)
    os.makedirs(snapshot_dir, exist_ok=True)
    original_dir = os.getcwd()
    os.chdir(REPO_DIR)
    create_snapshot_metafile(snapshot_hash, parent_commit_hash)
    snapshot_path = create_tar_zst_archive(snapshot_base, os.getcwd(), archive_entries)
    os.chdir(original_dir)
    if not os.path.exists(snapshot_path):
        raise FileNotFoundError(f"Snapshot not created at {snapshot_path}")

    manifest_path = f"{snapshot_base}.manifest.json"
    metadata_path = f"{snapshot_base}.metadata.json"
    diff_path = f"{snapshot_base}.diff.patch"
    archive_sha256 = sha256_file(snapshot_path)

    with open(manifest_path, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest_entries, manifest_file, indent=2, sort_keys=True)
    with open(diff_path, "w", encoding="utf-8") as diff_file:
        diff_file.write(diff_text)

    metadata = {
        "snapshot_id": snapshot_hash,
        "parent_commit": parent_commit_hash,
        "created_at": datetime.now().isoformat(),
        "archive_path": snapshot_path,
        "archive_sha256": archive_sha256,
        "archive_bytes": os.path.getsize(snapshot_path),
        "manifest_path": manifest_path,
        "diff_path": diff_path,
        "untracked_files": untracked_files,
    }
    with open(metadata_path, "w", encoding="utf-8") as metadata_file:
        json.dump(metadata, metadata_file, indent=2, sort_keys=True)

    logging.info(f"Snapshot created: {snapshot_path}")
    return {
        "snapshot_hash": snapshot_hash,
        "snapshot_path": snapshot_path,
        "manifest_path": manifest_path,
        "metadata_path": metadata_path,
        "archive_bytes": os.path.getsize(snapshot_path),
        "diff_path": diff_path,
    }

def load_config():
    global REPO_URL
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "r") as f:
            REPO_URL = f.read().strip()
        logging.info(f"Loaded REPO_URL: {REPO_URL}")
    return REPO_URL

def normalize_git_url(repo_url, commit_hash):
    """Convert SSH Git URL to HTTP GitHub URL with commit hash."""
    if repo_url.startswith("git@"):
        parts = repo_url.replace("git@", "").replace(".git", "").split(":")
        if len(parts) == 2:
            return f"https://{parts[0]}/{parts[1]}/commit/{commit_hash}"
    elif repo_url.startswith("https://"):
        return f"{repo_url.replace('.git', '')}/commit/{commit_hash}"
    return repo_url  # Fallback

@click.group()
def cli():
    init_db()
    load_config()
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    logging.info(f"MLflow tracking URI set to {MLFLOW_TRACKING_URI}")

@cli.command()
@click.argument("repo_url")
def init(repo_url):
    global REPO_URL
    REPO_URL = repo_url
    if os.path.exists(REPO_DIR):
        raise click.UsageError(f"Directory {REPO_DIR} already exists. Run 'cleanup' first.")
    subprocess.run(["git", "clone", repo_url, REPO_DIR], check=True)
    with open(CONFIG_PATH, "w") as f:
        f.write(repo_url)
    subprocess.run(["git", "fetch"], check=True, cwd=REPO_DIR)
    logging.info(f"Initialized AIline with {repo_url} in {REPO_DIR}")
    print(f"Initialized AIline with {repo_url} in {REPO_DIR}")

@cli.command
@click.option("--verbose", is_flag=True, help="Show all data about each experiment in a terminal")
def status(verbose):
    if not os.path.exists(DB_PATH):
        return "Database not found. Run 'ailine init' and 'ailine run' first.", 500
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url, dvc_linkage_json, dvc_linkage_status, env_fingerprint_json, env_fingerprint_status, run_command_json, run_command_summary FROM tree")
    rows = c.fetchall()
    tree = []
    for r in rows:
        linkage_payload = json.loads(r[8]) if r[8] else {"items": []}
        env_payload = json.loads(r[10]) if r[10] else {}
        run_command_payload = json.loads(r[12]) if r[12] else {}
        tree.append(
            {
                "id": r[0],
                "type": r[1],
                "parent": r[2],
                "mlflow_run": r[3],
                "dvc_version": r[4],
                "snapshot_path": r[5],
                "timestamp": r[6],
                "git_url": r[7],
                "dvc_linkage_status": r[9] or "missing",
                "dvc_linkage_count": len(linkage_payload.get("items", [])),
                "dvc_linkage_items": linkage_payload.get("items", []),
                "env_fingerprint_status": r[11] or "missing",
                "env_fingerprint": env_payload,
                "run_command_summary": r[13],
                "run_command_payload": run_command_payload,
            }
        )
    conn.close()
    if verbose:
        print_formatted_data(tree)
    else:
        print_table(tree)

@cli.command()
@click.option("--script", default="train.py", help="Script to run")
@click.option("--dataset", default="data.csv", help="Dataset file")
@click.option("--storage", default=DEFAULT_STORAGE_DIR, help="Directory to store snapshots")
def run(script, dataset, storage):
    if not REPO_URL:
        raise click.UsageError("AIline not initialized. Run 'ailine init <repo_url>' first.")
    if not os.path.exists(REPO_DIR):
        raise click.UsageError(f"Repo directory {REPO_DIR} not found. Re-run 'init'.")
    if not os.path.exists(os.path.join(REPO_DIR, script)):
        raise click.UsageError(f"Script {script} not found in {REPO_DIR}")
    if not os.path.exists(os.path.join(REPO_DIR, dataset)):
        raise click.UsageError(f"Dataset {dataset} not found in {REPO_DIR}")

    repo = git.Repo(REPO_DIR)
    latest_commit = repo.head.commit.hexsha
    full_commit_hash = repo.head.commit.hexsha
    git_url = normalize_git_url(REPO_URL, full_commit_hash)

    manifest_path = None
    metadata_path = None
    archive_bytes = None
    included_file_count = None
    excluded_file_count = None
    large_file_pointer_count = None
    diff_path = None

    if repo.is_dirty(untracked_files=True):
        policy = load_snapshot_policy()
        entries = scan_repo_files(REPO_DIR, policy)
        entries, _store = resolve_large_file_decisions(entries, policy)
        manifest_entries, archive_entries, manifest_extra = build_manifest(entries, storage)
        diff_text = repo.git.diff("HEAD")
        untracked_files = [normalize_rel_path(path) for path in repo.untracked_files]
        snapshot_result = create_snapshot(
            manifest_entries=manifest_entries,
            archive_entries=archive_entries,
            parent_commit_hash=latest_commit,
            storage_dir=storage,
            diff_text=diff_text,
            untracked_files=untracked_files,
        )
        commit_id = snapshot_result["snapshot_hash"]
        snapshot_path = snapshot_result["snapshot_path"]
        manifest_path = snapshot_result["manifest_path"]
        metadata_path = snapshot_result["metadata_path"]
        archive_bytes = snapshot_result["archive_bytes"]
        diff_path = snapshot_result["diff_path"]
        commit_type = CommitType.SNAPSHOT
        parent = latest_commit[:7]
        included_file_count = manifest_extra["summary"]["included_file_count"]
        excluded_file_count = manifest_extra["summary"]["excluded_file_count"]
        large_file_pointer_count = manifest_extra["summary"]["large_file_pointer_count"]
        click.echo(
            "Snapshot preflight: "
            f"files={len(entries)} included={included_file_count} "
            f"excluded={excluded_file_count} pointers={large_file_pointer_count} "
            f"included_bytes={manifest_extra['summary']['included_bytes']}"
        )
    else:
        commit_id = latest_commit
        commit_type = CommitType.GIT
        snapshot_path = None
        parent = None

    dvc_cfg = load_dvc_config()
    env_cfg = load_environment_config()
    run_capture_cfg = load_run_capture_config()
    original_dir = os.getcwd()
    os.chdir(REPO_DIR)
    subprocess.run(["dvc", "add", dataset], check=True)
    dvc_linkage = build_dvc_linkage(os.getcwd(), dvc_cfg)
    env_fingerprint, env_fingerprint_status = collect_environment_fingerprint(original_dir, env_cfg)
    run_command_payload, run_command_summary = build_run_command_payload(script, dataset, storage, os.getcwd())
    if not run_capture_cfg.get("enabled", True):
        run_command_payload = {}
        run_command_summary = None
    dvc_version = f"dataset_001_v{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    
    with mlflow.start_run(run_name=f"exp_{commit_id[:8]}"):
        subprocess.run(["python", script], check=True)
        mlflow.set_tag("commit" if commit_type == CommitType.GIT else CommitType.SNAPSHOT, commit_id)
        mlflow.set_tag("dataset", dvc_version)
        mlflow.set_tag("dvc_linkage_status", dvc_linkage["status"])
        mlflow.set_tag("env_fingerprint_status", env_fingerprint_status)
        mlflow.set_tag("run.script", script)
        mlflow.set_tag("run.dataset", dataset)
        run_id = mlflow.active_run().info.run_id
    
    os.chdir(original_dir)

    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        '''INSERT OR REPLACE INTO tree
           (id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url,
            manifest_path, metadata_path, archive_bytes, included_file_count, excluded_file_count,
            large_file_pointer_count, diff_path, dvc_linkage_json, dvc_linkage_status,
            env_fingerprint_json, env_fingerprint_status, run_command_json, run_command_summary)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (
            commit_id,
            commit_type.value,
            parent,
            run_id,
            dvc_version,
            snapshot_path,
            datetime.now().isoformat(),
            git_url if commit_type == CommitType.GIT else None,
            manifest_path,
            metadata_path,
            archive_bytes,
            included_file_count,
            excluded_file_count,
            large_file_pointer_count,
            diff_path,
            json.dumps(dvc_linkage, sort_keys=True),
            dvc_linkage["status"],
            json.dumps(env_fingerprint, sort_keys=True),
            env_fingerprint_status,
            json.dumps(run_command_payload, sort_keys=True) if run_command_payload else None,
            run_command_summary,
        ),
    )
    conn.commit()
    conn.close()
    logging.info(f"Experiment logged: {run_id} tied to {commit_id}")
    print(f"Experiment logged: {run_id} tied to {commit_id}")

@cli.command()
def cleanup():
    global REPO_URL
    items_to_remove = [MLFLOW_STORAGE_DIR, REPO_DIR, DB_PATH, CONFIG_PATH, DEFAULT_STORAGE_DIR]
    for item in os.listdir("."):
        if item.startswith("temp_") and os.path.isdir(item):
            items_to_remove.append(item)
    
    for item in items_to_remove:
        if os.path.isdir(item):
            shutil.rmtree(item, ignore_errors=True)
            logging.info(f"Removed directory: {item}")
            print(f"Removed directory: {item}")
        elif os.path.isfile(item):
            os.remove(item)
            logging.info(f"Removed file: {item}")
            print(f"Removed file: {item}")
    
    REPO_URL = None
    logging.info("Cleanup complete")
    print("Cleanup complete. Run 'ailine init <repo_url>' to start fresh.")

@app.route("/commits")
def commits():
    load_config()
    if not os.path.exists(DB_PATH):
        return "Database not found. Run 'ailine init' and 'ailine run' first.", 500
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT id, type, parent, mlflow_run, dvc_version, snapshot_path, timestamp, git_url FROM tree")
    tree = [{"id": r[0], "type": r[1], "parent": r[2], "mlflow_run": r[3], "dvc_version": r[4], 
             "snapshot_path": r[5], "timestamp": r[6], "git_url": r[7]} for r in c.fetchall()]
    conn.close()
    logging.info("Commits page accessed")
    return render_template("commits.html", tree=tree)

@app.route("/experiments")
def experiments():
    load_config()
    mlflow.set_tracking_uri(MLFLOW_TRACKING_URI)
    try:
        runs = mlflow.search_runs()
        runs_data = [{"run_id": r["run_id"], 
                      "mlflow_url": f"{MLFLOW_TRACKING_URI}/#/experiments/{r['experiment_id']}/runs/{r['run_id']}", 
                      "accuracy": r.get("metrics.accuracy", "N/A"), 
                      "commit": r.get("tags.commit"), 
                      "snapshot": r.get("tags.snapshot"), 
                      "dataset": r.get("tags.dataset", "N/A"), 
                      "timestamp": r.get("info.start_time", "N/A")} 
                     for r in runs.to_dict(orient="records")]
        logging.info(f"Experiments page accessed, found {len(runs_data)} runs")
        return render_template("experiments.html", runs=runs_data, repo_url=REPO_URL)
    except Exception as e:
        logging.error(f"Error in experiments route: {str(e)}")
        return f"Internal Server Error: {str(e)}", 500

@app.route("/commit/<commit_id>")
def commit_view(commit_id):
    load_config()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT git_url FROM tree WHERE id = ?", (commit_id,))
    result = c.fetchone()
    conn.close()
    if not result or not result[0]:
        logging.warning(f"Commit {commit_id} not found in database")
        return "Commit not found", 404

    git_url = result[0]
    try:
        repo = git.Repo(REPO_DIR)
        # Find the full commit hash
        full_commit_id = None
        for commit in repo.iter_commits():
            if commit.hexsha == commit_id or commit.hexsha.startswith(commit_id):
                full_commit_id = commit.hexsha
                break
        if not full_commit_id:
            logging.error(f"Commit {commit_id} not found in Git repository")
            return f"Commit {commit_id} not found in repository", 404

        logging.info(f"Commit view using read-only git object mode for {full_commit_id}")
        commit_files = repo.git.ls_tree("-r", full_commit_id, "--name-only").splitlines()
        logging.info(f"Files in commit {full_commit_id}: {commit_files}")
        files = []
        for rel_path in commit_files:
            path_parts = rel_path.split("/")
            if any(part.startswith(".") for part in path_parts):
                logging.info(f"Skipping hidden path in commit: {rel_path}")
                continue
            try:
                content = ensure_utf8_text(repo.git.show(f"{full_commit_id}:{rel_path}"))
                files.append({"path": rel_path, "content": content})
            except Exception as e:
                logging.warning(f"Unable to read commit object for {rel_path}: {str(e)}")
                files.append({"path": rel_path, "content": f"Binary or unreadable file: {rel_path}"})

        logging.info(f"Found {len(files)} files for commit {full_commit_id}")
        if not files:
            logging.warning(f"No readable non-hidden files found for commit {full_commit_id}")
            files.append({"path": "N/A", "content": "No files found in this commit."})

        return render_template("commit.html", commit_id=commit_id, files=files, git_url=git_url)
    except Exception as e:
        logging.error(f"Error in commit_view for {commit_id}: {str(e)}")
        return f"Error processing commit {commit_id}: {str(e)}", 500

@app.route("/snapshot/<snapshot_id>")
def snapshot_view(snapshot_id):
    load_config()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT snapshot_path, parent FROM tree WHERE id = ?", (snapshot_id,))
    result = c.fetchone()
    conn.close()
    if not result:
        logging.warning(f"Snapshot {snapshot_id} not found")
        return "Snapshot not found", 404
    
    snapshot_path, parent = result
    if not os.path.exists(snapshot_path):
        logging.error(f"Snapshot file not found at {snapshot_path}")
        return f"Snapshot file not found at {snapshot_path}", 500
    
    temp_dir = os.path.abspath(f"temp_{snapshot_id}")
    try:
        extract_tar_zst_archive(snapshot_path, temp_dir)
    except Exception as e:
        logging.error(f"Failed to unpack snapshot {snapshot_id}: {str(e)}")
        return f"Failed to unpack snapshot: {str(e)}", 500
    
    files = []
    for root, dirs, filenames in os.walk(temp_dir):
        if any(part.startswith('.') for part in root.split(os.sep)):
            continue
        for filename in filenames:
            if filename.startswith('.'):
                continue
            file_path = os.path.join(root, filename)
            rel_path = os.path.relpath(file_path, temp_dir)
            try:
                with open(file_path, "r", errors="ignore") as f:
                    content = f.read()
                files.append({"path": rel_path, "content": content})
            except Exception as e:
                files.append({"path": rel_path, "content": f"Error reading file: {str(e)}"})
    
    shutil.rmtree(temp_dir, ignore_errors=True)
    parent_url = normalize_git_url(REPO_URL, parent) if parent and REPO_URL else None
    logging.info(f"Viewed snapshot {snapshot_id}")
    return render_template("snapshot.html", snapshot_id=snapshot_id, files=files, parent_url=parent_url)

if __name__ == "__main__":
    start_mlflow_ui()
    cli()
    app.run(host="0.0.0.0", port=5000, debug=True)