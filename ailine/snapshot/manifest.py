"""Manifest construction: turns scan entries into a deterministic snapshot id."""

import hashlib
import json
from typing import List, Tuple

from ailine.snapshot.scan import create_large_file_pointer


def build_manifest(entries: List[dict], storage_dir: str) -> Tuple[List[dict], List[dict], dict]:
    manifest_entries: List[dict] = []
    archive_entries: List[dict] = []
    pointers: List[dict] = []
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
