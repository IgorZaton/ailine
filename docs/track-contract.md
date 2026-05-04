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
   content-addressed snapshot (manifest + diff + metadata + per-file objects)
   under the storage directory configured by `snapshot.storage_dir` in
   `.ailine.yml` (default: `<repo>/.ailine/snapshots`; overridable with the
   `AILINE_STORAGE_DIR` environment variable).
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
  storage_dir: .ailine/snapshots   # relative paths resolved against the repo root;
                                   # overridden by AILINE_STORAGE_DIR
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
- the MLflow run id (`wrap`: outer run; `inherit`: best-effort match to a run
  started by your script during the child process, when discoverable).

See [repro-contract.md](repro-contract.md) for the snapshot guarantees.

## Replay expectations (Python ML, best-effort)

AIline is aimed at **Python-centric ML** projects. For each run it stores what is
needed to argue the **next execution can be brought as close as is reasonable**
to the recorded one—not a hardware certification or a line-by-line audit of
every third-party library.

**What we maximize (today):**

- **Pre-run workspace state** under the Git work-tree: if the tree is “dirty”
  (including untracked files), a snapshot captures the included paths per
  policy (exclusions, large-file / DVC-pointer rules) **before** your `argv`
  starts. That is the strongest guarantee: “this is the code and local files
  the process could see at launch.”
- **How the run was launched:** exact `argv` and `cwd`.
- **Coarse environment:** Python version, platform, lockfile fingerprint, and
  configured package versions—not a bitwise copy of `site-packages`.
- **Data lineage where DVC is used:** linkage from existing `.dvc` /
  `dvc.yaml`—you still own remotes and pulls.
- **MLflow association** when available, for cross-checking metrics and
  artifacts in the tracking store.

**What we explicitly do not guarantee:**

- **Bit-identical reruns** of stochastic training (same seed ≠ same weights
  across GPUs, drivers, cuDNN settings, workers, etc.).
- **Files created only after** the snapshot step (e.g. checkpoints written mid-
  run, downloads during the run). Those are out of the pre-run archive unless
  you add a separate capture strategy later (e.g. DVC outputs, MLflow artifacts,
  post-run scan).
- **Reads outside the work-tree** or paths excluded by policy.
- **Hardware equivalence** or per-file verification of vendored / compiled
  dependencies beyond version metadata.

If you need stronger parity for **outputs**, treat them as first-class artifacts
(DVC `outs`, MLflow logged files, or a future post-run snapshot pass)—not as
something implied by the pre-run snapshot alone.

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
