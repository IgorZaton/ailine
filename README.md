# AIline

ML experiment lineage tracker with snapshot-based reproducibility.

AIline captures the **exact** code that produced an experiment — including
uncommitted changes — alongside DVC-managed data and MLflow run metadata, so
past experiments can be inspected and (eventually) re-run with confidence.

## Install (editable / development)

```bash
poetry install
# or, with pip
pip install -e .
```

## Quick start

```bash
ailine init <git_repo_url>          # clone the target repo
ailine run --script train.py        # snapshot + DVC linkage + MLflow run
ailine status                       # tabular view of recorded runs
ailine status --verbose             # per-run details, env, DVC items
```

By default MLflow writes runs to a **local file store** under `./mlruns` (no
tracking server required). Override with `AILINE_MLFLOW_URI` if you use a remote
or local REST tracking server.

For the Flask UI plus MLflow UI together (localhost tracking API on port 5001):

```bash
export AILINE_MLFLOW_URI=http://localhost:5001
ailine serve    # MLflow UI + Flask on :5001 / :5000 in one process
```

Then open `http://localhost:5000` for ailine and `http://localhost:5001` for MLflow.

## Configuration

| Env var | Purpose |
|--------|--------|
| `AILINE_MLFLOW_URI` | MLflow **tracking** backend (default: `file://…/mlruns` under the project) |
| `AILINE_MLFLOW_UI_BASE` | Base URL for **Run ID** links in the ailine web UI (default: `http://127.0.0.1:5001`). When unset and tracking is `http(s)`, same scheme/host as `AILINE_MLFLOW_URI` is used. |

Run links only work if an MLflow UI is reachable at that base URL (for example
`mlflow ui --backend-store-uri "$(pwd)/mlruns" --host 127.0.0.1 --port 5001`).

Project-level behaviour lives in `.ailine.yml` at the repository root
(snapshot exclusions, large-file policy, DVC mode, environment fingerprint
packages, run-capture toggle). See `docs/repro-contract.md` for the
reproducibility guarantees AIline aims to provide.

## Layout

```
ailine/
  cli/             # Click entry point + terminal formatters
  config/          # .ailine.yml loaders + defaults + path constants
  fingerprint/     # environment fingerprint
  integrations/    # MLflow UI subprocess, git URL helpers
  linkage/         # DVC discovery + linkage classification
  persistence/     # SQLite schema, migrations, repository facade
  run/             # CLI run-command capture
  snapshot/        # repo scan, manifest, tar.zst archive
  web/             # Flask app factory + route modules + templates
```
