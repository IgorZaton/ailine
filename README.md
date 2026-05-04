<img src="ailine/web/static/icon.png" alt="AIline logo" width="240" />

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

## Quick start (your own project)

```bash
pip install ailine                                  # or: poetry add --group dev ailine
cd /path/to/your/repo
ailine init-workspace                               # writes a default .ailine.yml
ailine doctor                                       # green-light all checks
ailine track -- python train.py --epochs 5          # run + record
ailine status --verbose                             # see what was captured
```

`ailine track --` is the primary, no-magic interface. It snapshots dirty
state, records DVC linkage + environment fingerprint + the exact argv, runs
your command in the repo root, and propagates the exit code. Your training
script keeps full ownership of MLflow runs (`track.mlflow.mode: inherit`).
See [docs/track-contract.md](docs/track-contract.md) for the schema.

### Demo / tutorial flow

The legacy "clone a sample repo and pretend-train" flow is still available
behind explicit `*-demo` commands:

```bash
ailine init-demo <git_repo_url>     # clone into ./repo
ailine run --script train.py        # demo: wraps in MLflow, records snapshot
ailine reset-demo                   # remove ./repo, DB, mlruns/
```

### CLI command summary

| Command | Purpose |
|---------|---------|
| `ailine init-workspace [--force]` | Bootstrap the pip-install workflow: write a default `.ailine.yml` and ensure state directories. No clone. |
| `ailine doctor [--json] [--strict] [--config PATH]` | Validate `.ailine.yml` and the local environment. The single source of truth for "is my setup OK". |
| `ailine track [--storage DIR] [--config PATH] [--run-name NAME] -- <argv...>` | Run a command under AIline tracking. The argv after `--` is executed verbatim from the repo root. |
| `ailine status [--verbose]` | List recorded runs: default output includes **full** `record_id` and `parent` lines (copy/paste for restore); `--verbose` dumps all fields. Errors clearly when the DB does not exist yet. |
| `ailine serve` | Start the MLflow UI subprocess and the Flask app together (ports 5001 and 5000). |
| `ailine init-demo <repo_url>` | Clone a sample repo into `./repo` and persist the URL in `ailine_config.txt` (tutorial flow). |
| `ailine run --script <s> [--dataset <d>] [--dvc-add] [--storage DIR]` | Demo wrapper around `track` that hard-codes `./repo` and forces `mlflow.mode=wrap`. |
| `ailine reset-demo` | Delete demo artifacts (`./repo`, DB, `mlruns/`, default snapshot dir, `temp_*`). |
| `ailine init` / `ailine cleanup` | Deprecated aliases for `init-demo` / `reset-demo`. |

By default MLflow writes runs to a **local file store** under `./mlruns` (no
tracking server required). Override with `AILINE_MLFLOW_URI` if you use a remote
or local REST tracking server.

For the Flask UI plus MLflow UI together (localhost tracking API on port 5001):

```bash
export AILINE_MLFLOW_URI=http://localhost:5001
ailine serve    # MLflow UI + Flask on :5001 / :5000 in one process
```

Then open `http://localhost:5000/` for ailine (the unified **Lineage** dashboard)
and `http://localhost:5001` for MLflow.

The legacy paths `http://localhost:5000/commits` and
`http://localhost:5000/experiments` now redirect (302) to `/` for backward
compatibility.

## Code browser (commit / snapshot views)

The `/commit/<id>` and `/snapshot/<id>` pages render a left-hand file tree with
a single-file blob view on the right. Use `?path=<rel/path>` to deep-link to a
specific file. Blobs and patches are capped at the first 512 KiB; oversized or
binary files are flagged in the header. Snapshots add a `?view=diff` tab that
renders the stored unified patch (`diff_path`) against the parent commit, split
into one card per file (split on `diff --git` headers) for readability.

## Configuration

| Env var | Purpose |
|--------|--------|
| `AILINE_MLFLOW_URI` | MLflow **tracking** backend (default: `file://…/mlruns` under the project) |
| `AILINE_MLFLOW_UI_BASE` | Base URL for **Run ID** links in the ailine web UI (default: `http://127.0.0.1:5001`). When unset and tracking is `http(s)`, same scheme/host as `AILINE_MLFLOW_URI` is used. |

Run links only work if an MLflow UI is reachable at that base URL (for example
`mlflow ui --backend-store-uri "$(pwd)/mlruns" --host 127.0.0.1 --port 5001`).

Project-level behaviour lives in `.ailine.yml` at the repository root
(snapshot exclusions, large-file policy, DVC linkage settings, environment
fingerprint packages, run-capture toggle, plus the new `project:` and
`track:` blocks for the `ailine track --` workflow).

- [docs/track-contract.md](docs/track-contract.md) — what `ailine track`
  guarantees and the full `.ailine.yml` schema.
- [docs/repro-contract.md](docs/repro-contract.md) — the snapshot
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

## Releasing

The package version is derived from the latest git tag via
[`poetry-dynamic-versioning`](https://github.com/mtkennerly/poetry-dynamic-versioning).
There is no manual `version = ...` bump in `pyproject.toml`; the tag *is* the
version.

One-time, on each developer machine:

```bash
poetry self add "poetry-dynamic-versioning[plugin]"
```

Local dry-run before tagging (runs tests, builds sdist + wheel, smoke-tests the
wheel in a throwaway venv):

```bash
bash scripts/release-check.sh
```

Cut a release:

```bash
git tag v0.2.0
git push origin v0.2.0
```

Pushing a `v*.*.*` tag triggers
[`.github/workflows/release.yml`](.github/workflows/release.yml), which runs the
test suite, calls `poetry build`, and publishes a GitHub Release with the
`dist/*.tar.gz` and `dist/*.whl` attached and auto-generated notes.

Pre-releases follow PEP 440 (matched by the configured tag pattern):
`v0.2.0a1`, `v0.2.0b2`, `v0.2.0rc1`.
