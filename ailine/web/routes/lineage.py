"""``/`` route — unified lineage dashboard.

Single primary surface for AIline. The main table mirrors what is recorded in
the SQLite ``tree`` and inlines all cross-links (commit/snapshot browser, Git
host, MLflow UI). A second collapsed section lists MLflow runs that are not
linked to any AIline tree row, so MLflow-only runs do not silently disappear.
"""

from __future__ import annotations

import logging
import os
from typing import Dict, List, Optional

import mlflow
from flask import Flask, render_template

from ailine.config import constants
from ailine.integrations.mlflow_links import get_mlflow_run_browser_context
from ailine.persistence import repository
from ailine.web.state import load_repo_url


_ORPHAN_RUN_LIMIT = 50


def _enrich_with_mlflow_urls(
    rows: List[dict], cache: Dict[str, tuple[Optional[str], Optional[str]]]
) -> None:
    for row in rows:
        run_id = row.get("mlflow_run")
        if not run_id:
            row["mlflow_url"] = None
            row["mlflow_run_name"] = None
            continue
        rid = str(run_id).strip()
        if rid not in cache:
            cache[rid] = get_mlflow_run_browser_context(rid)
        url, display_name = cache[rid]
        row["mlflow_url"] = url
        row["mlflow_run_name"] = display_name


def _collect_orphan_runs(
    linked_run_ids: set, cache: Dict[str, tuple[Optional[str], Optional[str]]]
) -> List[dict]:
    """MLflow runs whose ``run_id`` is not present in any AIline tree row."""
    try:
        mlflow.set_tracking_uri(constants.MLFLOW_TRACKING_URI)
        df = mlflow.search_runs(
            order_by=["start_time DESC"], max_results=_ORPHAN_RUN_LIMIT
        )
    except Exception as exc:
        logging.debug("Orphan MLflow lookup failed: %s", exc)
        return []
    if df is None or getattr(df, "empty", True):
        return []

    orphans: List[dict] = []
    for record in df.to_dict(orient="records"):
        run_id = record.get("run_id")
        if not run_id or run_id in linked_run_ids:
            continue
        rid = str(run_id).strip()
        if rid not in cache:
            cache[rid] = get_mlflow_run_browser_context(rid)
        url, display_name = cache[rid]
        tag_name = record.get("tags.mlflow.runName") or record.get("mlflow.runName")
        orphans.append(
            {
                "run_id": run_id,
                "experiment_id": record.get("experiment_id"),
                "start_time": record.get("start_time"),
                "commit_tag": record.get("tags.commit"),
                "snapshot_tag": record.get("tags.snapshot"),
                "mlflow_url": url,
                "mlflow_run_name": tag_name or display_name,
            }
        )
    return orphans


def _promote_matching_orphans_into_rows(rows: List[dict], orphans: List[dict]) -> List[dict]:
    """Attach likely matching orphan runs to in-progress lineage rows.

    In ``inherit`` mode AIline can discover the MLflow run id only after the
    child exits, so the active run may appear in the orphan section first.
    For better live UX we best-effort match by tags:
    - orphan ``tags.snapshot`` == lineage row id for snapshot rows
    - orphan ``tags.commit`` == lineage row id for git rows

    Matched orphans are moved into the corresponding row's MLflow column and
    removed from the orphan list to avoid duplicate display.
    """
    if not orphans:
        return orphans

    remaining: List[dict] = []
    for orphan in orphans:
        run_id = orphan.get("run_id")
        commit_tag = orphan.get("commit_tag")
        snapshot_tag = orphan.get("snapshot_tag")
        matched = False

        for row in rows:
            if row.get("mlflow_run"):
                continue
            status = str(row.get("status") or "").strip().lower()
            if status != "in_progress":
                continue
            row_id = row.get("id")
            row_type = row.get("type")
            if row_type == "snapshot" and snapshot_tag and snapshot_tag == row_id:
                row["mlflow_run"] = run_id
                row["mlflow_url"] = orphan.get("mlflow_url")
                row["mlflow_run_name"] = orphan.get("mlflow_run_name")
                matched = True
                break
            # Common inherit-mode case for dirty trees:
            # - lineage row id = snapshot hash
            # - MLflow run tags only include the git commit SHA
            # In that scenario, bind by snapshot row parent commit.
            if (
                row_type == "snapshot"
                and commit_tag
                and row.get("parent")
                and commit_tag == row.get("parent")
            ):
                row["mlflow_run"] = run_id
                row["mlflow_url"] = orphan.get("mlflow_url")
                row["mlflow_run_name"] = orphan.get("mlflow_run_name")
                matched = True
                break
            if row_type == "git" and commit_tag and commit_tag == row_id:
                row["mlflow_run"] = run_id
                row["mlflow_url"] = orphan.get("mlflow_url")
                row["mlflow_run_name"] = orphan.get("mlflow_run_name")
                matched = True
                break

        if not matched:
            remaining.append(orphan)

    # Last-resort UX fallback for "exactly one active run" view:
    # if there is a single in-progress row with empty mlflow_run and a single
    # remaining orphan, attach it so users see a live link immediately.
    if len(remaining) == 1:
        in_progress_without_link = [
            row
            for row in rows
            if not row.get("mlflow_run")
            and str(row.get("status") or "").strip().lower() == "in_progress"
        ]
        if len(in_progress_without_link) == 1:
            row = in_progress_without_link[0]
            orphan = remaining[0]
            row["mlflow_run"] = orphan.get("run_id")
            row["mlflow_url"] = orphan.get("mlflow_url")
            row["mlflow_run_name"] = orphan.get("mlflow_run_name")
            return []

    return remaining


def view():
    load_repo_url()
    if not os.path.exists(constants.DB_PATH):
        return (
            "Database not found. Run 'ailine init-workspace' (or 'ailine init-demo') "
            "and 'ailine track --' (or 'ailine run') first.",
            500,
        )

    rows = repository.fetch_commits_overview()
    cache: Dict[str, tuple[Optional[str], Optional[str]]] = {}
    _enrich_with_mlflow_urls(rows, cache)

    linked_run_ids = {r["mlflow_run"] for r in rows if r.get("mlflow_run")}
    orphans = _collect_orphan_runs(linked_run_ids, cache)
    orphans = _promote_matching_orphans_into_rows(rows, orphans)

    logging.info(
        "Lineage page accessed (rows=%d, orphans=%d)", len(rows), len(orphans)
    )
    return render_template("lineage.html", rows=rows, orphans=orphans)


def register(app: Flask) -> None:
    app.add_url_rule("/", endpoint="lineage", view_func=view)
