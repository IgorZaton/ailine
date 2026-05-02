"""Persist the exact CLI invocation that produced an experiment."""

from datetime import datetime
from typing import Tuple


def build_run_command_payload(
    script: str,
    dataset: str,
    storage: str,
    repo_cwd: str,
) -> Tuple[dict, str]:
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
