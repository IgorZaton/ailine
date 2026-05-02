# `ailine track` contract

`ailine track -- <argv>` is the primary way to add reproducibility tracking to
an existing ML project without rewriting your training code. This doc
defines the **contract**: what AIline guarantees, what it expects from your
project, and how `.ailine.yml` configures the boundary.

## TL;DR

```bash
pip install ailine
cd /path/to/your/repo
ailine init-workspace          # writes a default .ailine.yml (review it)
ailine doctor                  # green-light all checks
ailine track -- python train.py --epochs 5
```

The first token after `--` is the **program** to execute (looked up on `PATH`
or as a path). It is not a shell: `ailine track -- repo/train.py` fails
because `repo/train.py` is not an executable binary. Use
`ailine track -- python repo/train.py` (or `chmod +x` and a shebang).

`track`:

1. Walks parent directories from cwd to find a Git work-tree (`.git`).
2. Loads and validates `.ailine.yml` at the work-tree root.
3. If your tree is dirty (uncommitted changes / untracked files), creates a
   content-addressed snapshot (`tar.zst` + manifest + diff + metadata) under
   the configured `--storage` directory.
4. Captures DVC linkage, environment fingerprint, the exact argv, and your
   Git state into the SQLite tree.
5. Runs `python train.py --epochs 5` (or whatever you put after `--`) as a
   subprocess from the work-tree, **propagating the exit code**.

## What AIline does NOT do

- **No magic auto-detection** of frameworks. Your script keeps full control.
- **No silent `dvc add`**. AIline only records DVC linkage from existing
  `.dvc` files / `dvc.yaml`. You own dataset versioning.
- **No outer MLflow run** by default. If your training script calls
  `mlflow.start_run`, that run is the run; AIline just records that it
  happened. See `track.mlflow.mode` below.
- **No edits to your code or working tree.** The snapshot is read-only.

## `.ailine.yml` schema (v1)

The file lives at the Git work-tree root. All sections are optional; defaults
listed below are used when a key is omitted.

```yaml
project:
  version: 1                # required if present; only v1 is supported today
  mode: track               # track | demo

track:
  repo_root: auto           # 'auto' walks parents for .git, or absolute path
  mlflow:
    mode: inherit           # inherit | wrap | none
    set_env: false          # if true, ailine sets MLFLOW_TRACKING_URI before child
  dvc:
    verify: "off"           # off | warn | strict
    verify_commands: []     # e.g. [["dvc", "status", "--quiet"]]

snapshot:
  exclude_globs:            # gitignore-style globs, applied to repo files
    - ".git/**"
    - ".venv/**"
    - "__pycache__/**"
    - "*.pyc"
    - "mlruns/**"
    - ".ailine/**"
  large_file_mb: 50         # files at or above this size go through large-file policy
  large_file_mode: prompt   # prompt | skip | include
  dvc_pointer_patterns:
    - "*.dvc"

dvc:
  remote_name:              # null = first available remote, or set explicitly
  require_hash_fields: true # if true, missing md5/etc downgrades linkage to 'partial'
  ignore_paths: []          # globs of DVC outputs to skip during linkage discovery

environment:
  enabled: true
  packages:                 # versions captured into the env fingerprint
    - mlflow
    - dvc

run_capture:
  enabled: true             # persist full argv + cwd + timestamp per run
```

### `track.mlflow.mode`

| Mode      | What ailine does                                                                                          | When to use                                                                |
|-----------|------------------------------------------------------------------------------------------------------------|----------------------------------------------------------------------------|
| `inherit` | Nothing. Your script's own `mlflow.start_run` is the run.                                                  | Default. Most modern training scripts already use MLflow.                  |
| `wrap`    | Opens an outer `mlflow.start_run(run_name=...)` around your child.                                         | Quick wins for scripts that do not log to MLflow themselves.               |
| `none`    | No MLflow side effects whatsoever (does not even resolve `MLFLOW_TRACKING_URI`).                           | When you do not use MLflow at all and want zero coupling.                  |

If `track.mlflow.set_env: true`, AIline sets `MLFLOW_TRACKING_URI` in the
child environment before spawning. Useful when you do not want to bake the
URI into your training code.

### `track.dvc.verify`

`verify_commands` is a list of argv lists. Before running your child, AIline
runs each one from the repo root.

| `verify` | Behaviour on non-zero exit                          |
|----------|------------------------------------------------------|
| `off`    | Commands are NOT run.                                |
| `warn`   | Logs a warning; child still runs.                    |
| `strict` | Aborts with a `SessionError` before child starts.    |

Example: enforce `dvc status` is clean before training.

```yaml
track:
  dvc:
    verify: strict
    verify_commands:
      - ["dvc", "status", "--quiet"]
```

## Reproducibility per run

For every recorded run AIline persists:

- `commit` (git sha) **or** `snapshot` (content-addressed snapshot id),
- the diff against parent commit (snapshots only),
- the manifest of every included/excluded/pointer file,
- DVC linkage (paths, hashes, cache/remote presence),
- environment fingerprint (Python, platform, `poetry.lock` sha, package versions),
- the exact argv + cwd of the child process,
- the MLflow run id (when `mode == 'wrap'`; `inherit` records via tags inside
  your script).

See [repro-contract.md](repro-contract.md) for the snapshot guarantees.

## Migrating from `ailine run`

`ailine run` (the demo flow) keeps working but is now a thin wrapper around
the same session as `ailine track`. The differences:

| Concern              | `ailine run` (demo)                  | `ailine track`                                  |
|----------------------|--------------------------------------|--------------------------------------------------|
| Repo location        | hard-coded `./repo`                  | resolved from cwd or `track.repo_root`           |
| Command              | hard-coded `python <script>`         | arbitrary argv after `--`                        |
| MLflow               | always `wrap`                         | per `track.mlflow.mode` (default `inherit`)      |
| `dvc add`            | opt-in via `--dvc-add` (off by default)| never (you own DVC)                              |
| Required pre-step    | `ailine init-demo <repo_url>`        | `ailine init-workspace` (writes `.ailine.yml`)   |

For your own projects, prefer `ailine track --`.
